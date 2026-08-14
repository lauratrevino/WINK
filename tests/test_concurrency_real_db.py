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
