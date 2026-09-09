from datetime import datetime

from ..errors import log_error


def log_demo_session_ended(cur, student_id, reason):
    """Records a completed demo run in demo_sessions (duration, questions
    asked, why it ended) so it shows up in the admin Demo Usage stats.

    Runs inside a SAVEPOINT, not just a bare try/except: if any statement
    below fails against Postgres, the whole transaction is poisoned
    (Postgres refuses every further command until a rollback) even though
    `except Exception: pass` would look like it safely swallowed the
    error. The caller's NEXT statement in the same transaction (the
    is_active=FALSE UPDATE right after this call) would then fail too,
    with an unrelated-looking "current transaction is aborted" error —
    which is exactly what happened when demo_sessions was once missing a
    column this INSERT expected. Rolling back to a savepoint undoes only
    this function's own statements, leaving whatever the caller already
    did earlier in the same transaction intact.
    """
    cur.execute("SAVEPOINT log_demo_session_ended")
    try:
        cur.execute("SELECT created_at FROM students WHERE id=%s", (student_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("RELEASE SAVEPOINT log_demo_session_ended")
            return
        started_at = row["created_at"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE student_id=%s AND event_type='question_asked'",
                    (student_id,))
        questions_asked = cur.fetchone()["n"] or 0
        duration_seconds = max(0, int((datetime.utcnow() - started_at).total_seconds()))
        cur.execute("""INSERT INTO demo_sessions(started_at, ended_at, duration_seconds, questions_asked, ended_reason, student_id)
                       VALUES (%s, NOW(), %s, %s, %s, %s)""",
                    (started_at, duration_seconds, questions_asked, reason, student_id))
        cur.execute("RELEASE SAVEPOINT log_demo_session_ended")
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT log_demo_session_ended")
        log_error("services.demo.log_session_ended", e, student_id=student_id)


def end_demo_session(cur, student_id, reason):
    """The one non-destructive way a demo session is ever ended, used by
    every caller (logout, the daily purge cron, and a lazy expiry check
    hit mid-request) so they can't drift apart again. Nothing about the
    account is deleted — the row, its seeded/uploaded documents, its
    events, its conversations, its answer_logs are all kept indefinitely
    so every demo run stays visible in Analytics (statistics + full
    conversation content), not just a one-line summary. is_active=FALSE
    is what actually stops the account from being treated as a live demo
    session again (matched by _purge_expired, and by current_student()'s
    own expiry check) without destroying anything it produced. Demo
    accounts never collect a real name, email, or other identifying
    information in the first place (see demo.py's _seed_demo/start_demo),
    so keeping this data indefinitely does not retain anything personally
    identifying — only the same usage/interaction data a registered
    student's account accumulates.
    """
    log_demo_session_ended(cur, student_id, reason)
    cur.execute("UPDATE students SET is_active=FALSE WHERE id=%s AND is_demo=TRUE", (student_id,))
