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
