"""Regression coverage for demo-account data retention.

demo.py's _purge_expired()/delete_demo_student() (and the shared
services/demo.py helper they now both call through) are explicit that an
ended demo session is never deleted -- only marked inactive -- so every
demo run stays visible in Analytics the same way a registered student's
activity does, just without any real identifying information (a demo
account never collects a real name or email to begin with).

security.py's current_student() used to bypass that entirely: touching an
already-expired demo session (e.g. an old tab, or the same demo link
revisited after the 6-hour TTL) hard-deleted the account's events and the
student row itself, silently destroying its documents/deadlines/
conversations/answer_logs via ON DELETE CASCADE and never recording a
demo_sessions summary row -- so an expired demo's data vanished before
the daily purge cron or the admin Analytics page ever saw it. This file
pins down the fixed, non-destructive behavior.
"""
import psycopg2
import psycopg2.extras


def _db():
    import os
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    return conn


def _start_demo(client):
    resp = client.post("/demo/start", follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        return sess["sid"]


def _expire_demo(sid):
    conn = _db()
    cur = conn.cursor()
    cur.execute("UPDATE students SET demo_expires_at = NOW() - INTERVAL '1 hour' WHERE id=%s", (sid,))
    cur.close(); conn.close()


class TestExpiredDemoAccessDoesNotDestroyData:
    def test_visiting_an_expired_demo_session_keeps_its_data(self, client):
        sid = _start_demo(client)
        _expire_demo(sid)

        # Any authenticated page load re-runs current_student(), which is
        # where the expiry check (and, previously, the destructive
        # cleanup) lives.
        resp = client.get("/chat-page", follow_redirects=False)
        # The session is no longer valid -- current_student() correctly
        # stops treating it as logged in -- but nothing behind it should
        # have been deleted.
        assert resp.status_code in (302, 401)

        conn = _db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT is_active, is_demo FROM students WHERE id=%s", (sid,))
        student = cur.fetchone()
        assert student is not None, "expired demo account row must not be hard-deleted"
        assert student["is_active"] is False
        assert student["is_demo"] is True

        cur.execute("SELECT COUNT(*) as n FROM documents WHERE student_id=%s", (sid,))
        assert cur.fetchone()["n"] > 0, "seeded demo documents must survive expiry"

        cur.execute("SELECT COUNT(*) as n FROM events WHERE student_id=%s", (sid,))
        assert cur.fetchone()["n"] > 0, "demo events must survive expiry, not be wiped"

        cur.execute("SELECT COUNT(*) as n FROM conversations WHERE student_id=%s", (sid,))
        assert cur.fetchone()["n"] > 0, "seeded demo conversation must survive expiry"

        cur.execute("SELECT ended_reason FROM demo_sessions WHERE student_id=%s", (sid,))
        session_row = cur.fetchone()
        assert session_row is not None, "expiring a demo session must record a demo_sessions row"
        assert session_row["ended_reason"] == "expired"
        cur.close(); conn.close()

    def test_expired_demo_session_no_longer_matches_purge_expired(self, client):
        # Once current_student() has ended the session, the daily purge
        # job's own query (is_active=TRUE AND demo_expires_at < NOW())
        # must not pick it up again and double-log it.
        sid = _start_demo(client)
        _expire_demo(sid)
        client.get("/chat-page", follow_redirects=False)

        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as n FROM students WHERE id=%s AND is_demo=TRUE AND is_active=TRUE "
                    "AND demo_expires_at < NOW()", (sid,))
        assert cur.fetchone()[0] == 0
        cur.close(); conn.close()


def _register_admin(client):
    from conftest import mark_email_verified
    client.post("/register", data={
        "email": "admin@utep.edu", "password": "password123",
        "first_name": "Ada", "last_name": "Lovelace", "classification": "Senior",
        "major": "Computer Science", "university": "University of Texas at El Paso",
        "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
    }, follow_redirects=False)
    mark_email_verified("admin@utep.edu")


def _backdate(sid, days):
    conn = _db()
    cur = conn.cursor()
    cur.execute("UPDATE students SET created_at = NOW() - make_interval(days => %s) WHERE id=%s", (days, sid))
    cur.execute("UPDATE demo_sessions SET started_at = NOW() - make_interval(days => %s) WHERE student_id=%s",
                (days, sid))
    cur.close(); conn.close()


class TestPurgeOldDemoData:
    def test_non_admin_cannot_purge(self, client):
        from conftest import mark_email_verified
        client.post("/register", data={
            "email": "notadmin@utep.edu", "password": "password123",
            "first_name": "Ada", "last_name": "Lovelace", "classification": "Senior",
            "major": "Computer Science", "university": "University of Texas at El Paso",
            "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
        }, follow_redirects=False)
        mark_email_verified("notadmin@utep.edu")
        resp = client.post("/purge-old-demo-data", json={"confirm": "PURGE"})
        assert resp.status_code == 403

    def test_wrong_confirmation_phrase_deletes_nothing(self, client):
        old_sid = _start_demo(client)
        _expire_demo(old_sid)
        client.get("/chat-page", follow_redirects=False)
        _backdate(old_sid, 3)
        client.post("/logout")
        _register_admin(client)

        resp = client.post("/purge-old-demo-data", json={"confirm": "nope"})
        assert resp.status_code == 400

        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as n FROM students WHERE id=%s", (old_sid,))
        assert cur.fetchone()[0] == 1, "nothing should be deleted without the exact confirmation phrase"
        cur.close(); conn.close()

    def test_purge_deletes_old_demo_data_but_keeps_todays_and_real_students(self, client):
        # An old, already-ended demo account (its whole history -- row,
        # events, documents, conversations, demo_sessions -- backdated 3
        # days, the way a real leftover from earlier testing would look).
        old_sid = _start_demo(client)
        _expire_demo(old_sid)
        client.get("/chat-page", follow_redirects=False)
        _backdate(old_sid, 3)
        client.post("/logout")

        # A fresh demo account created today, which must survive the purge.
        today_sid = _start_demo(client)
        client.post("/logout")

        _register_admin(client)

        resp = client.post("/purge-old-demo-data", json={"confirm": "PURGE"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["success"] is True
        assert body["demo_accounts_deleted"] == 1
        assert body["demo_sessions_deleted"] == 1

        conn = _db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT id FROM students WHERE id=%s", (old_sid,))
        assert cur.fetchone() is None, "the old demo account row should be gone"
        cur.execute("SELECT COUNT(*) as n FROM events WHERE student_id=%s", (old_sid,))
        assert cur.fetchone()["n"] == 0, "the old demo account's dangling events should be gone too"
        cur.execute("SELECT COUNT(*) as n FROM demo_sessions WHERE student_id=%s", (old_sid,))
        assert cur.fetchone()["n"] == 0, "its demo_sessions history should be gone, not just orphaned"

        cur.execute("SELECT id, is_demo FROM students WHERE id=%s", (today_sid,))
        today_row = cur.fetchone()
        assert today_row is not None, "today's demo account must survive the purge"
        assert today_row["is_demo"] is True
        # Logging out of today_sid's demo above ends its session (see
        # auth.py's logout route), which does record a demo_sessions row
        # -- but with started_at == today, so the purge must have kept
        # it rather than deleting it along with the old one.
        cur.execute("SELECT COUNT(*) as n FROM demo_sessions WHERE student_id=%s", (today_sid,))
        assert cur.fetchone()["n"] == 1, "today's own demo_sessions row must survive the purge"

        cur.execute("SELECT id FROM students WHERE email='admin@utep.edu' AND is_demo IS NOT TRUE")
        assert cur.fetchone() is not None, "the real admin account must never be touched by a demo purge"
        cur.close(); conn.close()
