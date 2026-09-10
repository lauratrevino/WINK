from datetime import datetime

from .. import config
from ..errors import log_error
from .analytics import safe_payload


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
        # _seed_demo backdates ~24 fake "question_asked" events per demo
        # account (see its comment) so a fresh visitor's dashboard looks
        # populated. Those aren't real questions, so they're excluded here
        # via the "seeded" payload flag -- a plain COUNT(*) would credit
        # every demo session with ~24 questions nobody actually asked.
        cur.execute("SELECT payload FROM events WHERE student_id=%s AND event_type='question_asked'",
                    (student_id,))
        questions_asked = sum(1 for r in cur.fetchall() if not safe_payload(r["payload"]).get("seeded"))
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


def purge_demo_data_before_today(cur):
    """Wipes out every demo account and demo_sessions row from before
    "today" (in APP_TIMEZONE, matching the day boundary Deadlines already
    uses), keeping only demo activity from today. This is a manual,
    admin-triggered cleanup -- unlike end_demo_session()/purge_expired
    above, which deliberately keep demo data forever for Analytics, this
    exists because the accumulated history of a developer's own repeated
    demo testing skews the Demo tab's aggregate stats once real pilot
    data starts coming in, and there's no other way to clear that out.

    Three groups of rows need handling, because demo data doesn't all
    hang off the students row the way a real account's does:

    1. events / document_chunks have no foreign key to students at all
       (see delete_student()'s docstring in blueprints/admin.py) --
       deleting the student row would just leave these dangling, so they
       have to be deleted explicitly by student_id first.
    2. Every other student-owned table (documents, deadlines,
       conversations, practice_questions, grading_weights, answer_logs,
       course_colors, token_usage) has ON DELETE CASCADE and is cleaned
       up automatically when the students row goes.
    3. demo_sessions is deliberately designed to outlive its student row
       (ON DELETE SET NULL -- see migration f4b8c1e9a273) so Analytics
       stats don't lose history if a demo account is hard-deleted some
       other way. That means it's the one table that must be purged by
       its OWN date (started_at), not by chasing student_id, or old
       sessions whose student_id was already nulled out some other time
       would be left behind still counted in the Demo tab's totals.

    Returns a dict of counts for the confirmation toast.
    """
    cur.execute("""SELECT id FROM students
                   WHERE is_demo=TRUE AND created_at < (NOW() AT TIME ZONE %s)::date""",
                (config.APP_TIMEZONE,))
    target_ids = [r["id"] for r in cur.fetchall()]

    events_deleted = 0
    chunks_deleted = 0
    if target_ids:
        cur.execute("DELETE FROM events WHERE student_id = ANY(%s)", (target_ids,))
        events_deleted = cur.rowcount
        cur.execute("DELETE FROM document_chunks WHERE student_id = ANY(%s)", (target_ids,))
        chunks_deleted = cur.rowcount
        cur.execute("DELETE FROM students WHERE id = ANY(%s)", (target_ids,))

    cur.execute("""DELETE FROM demo_sessions
                   WHERE started_at < (NOW() AT TIME ZONE %s)::date""",
                (config.APP_TIMEZONE,))
    sessions_deleted = cur.rowcount

    return {
        "demo_accounts_deleted": len(target_ids),
        "demo_sessions_deleted": sessions_deleted,
        "events_deleted": events_deleted,
        "document_chunks_deleted": chunks_deleted,
    }
