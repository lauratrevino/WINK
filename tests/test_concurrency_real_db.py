import json
import threading
import time

import pytest

from conftest import mark_email_verified


class SlowFakeStream:
    def __init__(self, delay):
        self.delay = delay

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        time.sleep(self.delay)
        yield "Answer."


class SlowFakeMessages:
    def __init__(self, delay):
        self.delay = delay

    def stream(self, **kwargs):
        return SlowFakeStream(self.delay)


class SlowFakeClient:
    def __init__(self, delay=0.5):
        self.messages = SlowFakeMessages(delay)


@pytest.fixture()
def small_pool(app, monkeypatch):
    from psycopg2 import pool as pg_pool
    from psycopg2.extras import RealDictCursor
    from wink import config, extensions

    small = pg_pool.ThreadedConnectionPool(1, 3, config.DB_URL, cursor_factory=RealDictCursor)
    monkeypatch.setattr(extensions, "_db_pool", small)
    yield small
    small.closeall()


def test_many_concurrent_slow_chats_dont_starve_a_small_pool(app, client, monkeypatch, small_pool):
    import wink.blueprints.chat as chat_bp
    import wink.config as config

    monkeypatch.setattr(chat_bp, "anthropic_client", SlowFakeClient(delay=0.5))
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")

    N = 8
    authed_clients = []
    for i in range(N):
        c = app.test_client()
        email = f"loadtest{i}@utep.edu"
        r = c.post("/register", data={
            "email": email, "password": "password123",
            "first_name": "Load", "last_name": f"Test{i}",
            "classification": "Senior", "major": "Computer Science", "university": "University of Texas at El Paso",
            "terms_agree": "on", "research_agree": "on",
        })
        assert r.status_code == 302
        mark_email_verified(email)
        authed_clients.append(c)

    results = {}
    errors = {}

    def do_chat(i, c):
        try:
            start = time.time()
            resp = c.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
            elapsed = time.time() - start
            results[i] = (resp.status_code, elapsed)
        except Exception as e:
            errors[i] = e

    threads = [threading.Thread(target=do_chat, args=(i, authed_clients[i])) for i in range(N)]
    overall_start = time.time()
    for t in threads:
        t.start()
        time.sleep(0.02)  
    for t in threads:
        t.join(timeout=15)
    overall_elapsed = time.time() - overall_start

    assert not errors, f"some requests raised exceptions: {errors}"
    assert len(results) == N, f"expected all {N} requests to complete, got {len(results)}: {results}"
    for i, (status, elapsed) in results.items():
        assert status == 200, f"request {i} failed with status {status}"

    print(f"\n{N} concurrent 0.5s-streaming chat requests against a 3-connection pool "
          f"finished in {overall_elapsed:.2f}s")
    assert overall_elapsed < 1.5, (
        f"expected all requests to run concurrently (~0.5-0.7s total); "
        f"took {overall_elapsed:.2f}s, suggesting a connection is being held "
        f"during streaming and requests are queuing for the pool"
    )


def test_concurrent_password_reset_with_same_token_only_succeeds_once(app, monkeypatch):
    """Regression test for a real race: two simultaneous requests using the
    same valid reset token used to both be able to pass the "is it used?"
    check before either one committed marking it used. Fixed by making the
    claim atomic (UPDATE ... WHERE used=FALSE RETURNING ...) — this proves
    it with real concurrent threads, not just by reading the code."""
    import hashlib
    import secrets as secrets_mod
    from wink.extensions import get_db

    with app.app_context():
        conn = get_db(); cur = conn.cursor()
        cur.execute("""INSERT INTO students(email,password_hash,first_name,last_name,classification,major,university)
                       VALUES('racetest@utep.edu','oldhash','Race','Test','Senior','Computer Science',
                       'University of Texas at El Paso') RETURNING id""")
        sid = cur.fetchone()["id"]
        raw_token = secrets_mod.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        cur.execute("""INSERT INTO password_resets(student_id, token, expires_at)
                       VALUES(%s, %s, NOW() + INTERVAL '1 hour')""", (sid, token_hash))
        conn.commit(); cur.close()

    results = []
    lock = threading.Lock()

    def attempt_reset():
        c = app.test_client()
        resp = c.post(f"/reset-password/{raw_token}", data={
            "password": "newpassword123", "confirm_password": "newpassword123",
        })
        body = resp.get_data(as_text=True)
        with lock:
            results.append("success" if "has been updated" in body else "rejected")

    threads = [threading.Thread(target=attempt_reset) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 10
    assert results.count("success") == 1, (
        f"expected exactly ONE of 10 simultaneous requests with the same token to succeed, "
        f"got {results.count('success')} successes: {results}"
    )
    assert results.count("rejected") == 9

    with app.app_context():
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT used FROM password_resets WHERE student_id=%s", (sid,))
        assert cur.fetchone()["used"] is True
        cur.close()


def test_concurrent_mfa_backup_code_use_only_succeeds_once(app):
    """Same class of race as the password-reset test above, for MFA backup
    codes: two simultaneous requests using the same backup code used to
    both be able to read the same pre-request snapshot of the code list
    and both believe they'd consumed it. Fixed with SELECT ... FOR UPDATE
    to lock the row for the check-and-remove sequence."""
    import pyotp
    from werkzeug.security import generate_password_hash
    from wink.extensions import get_db

    plain_code = "raceb4ck"
    with app.app_context():
        conn = get_db(); cur = conn.cursor()
        cur.execute("""INSERT INTO students(email,password_hash,first_name,last_name,classification,major,university,
                       mfa_enabled, mfa_secret, mfa_backup_codes)
                       VALUES('mfaracetest@utep.edu',%s,'MFA','Race','Senior','Computer Science',
                       'University of Texas at El Paso', TRUE, %s, %s) RETURNING id""",
                    (generate_password_hash("password123"), pyotp.random_base32(),
                     json.dumps([generate_password_hash(plain_code)])))
        conn.commit(); cur.close()

    results = []
    lock = threading.Lock()

    def attempt_verify():
        c = app.test_client()
        c.post("/login", data={"email": "mfaracetest@utep.edu", "password": "password123"})
        resp = c.post("/mfa/verify", data={"code": plain_code}, follow_redirects=False)
        with lock:
            results.append("success" if resp.headers.get("Location", "").endswith("dashboard") else "rejected")

    threads = [threading.Thread(target=attempt_verify) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 10
    assert results.count("success") == 1, (
        f"expected exactly ONE of 10 simultaneous requests with the same backup code to succeed, "
        f"got {results.count('success')} successes: {results}"
    )

    with app.app_context():
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT mfa_backup_codes FROM students WHERE email='mfaracetest@utep.edu'")
        remaining = json.loads(cur.fetchone()["mfa_backup_codes"])
        assert remaining == [], f"backup code should be fully consumed, but {len(remaining)} remain"
        cur.close()
