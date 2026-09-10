import json
import secrets
from datetime import timedelta

from werkzeug.security import generate_password_hash

from .. import config
from ..errors import log_error
from ..extensions import db_cursor
from .pricing import estimate_cost_usd

# Admin accounts (config.ADMIN_EMAILS) are ordinary rows in the `students`
# table -- there's no separate is_admin column -- so every "real student"
# aggregate in this module has to exclude them explicitly, the same way
# is_demo excludes demo accounts. Without this, an admin/TA's own dev/
# testing account (often the oldest account, with disproportionate logins,
# questions, and token usage from building/testing WINK) gets counted as a
# "real student" and skews these numbers -- exactly the kind of thing that
# makes a stat like "time to first question" look wrong with a small pilot
# cohort, where one such account can dominate an average.
_ADMIN_EMAILS = list(config.ADMIN_EMAILS)


def log_event(sid, etype, payload=None):
    if not config.DB_URL:
        return
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO events(student_id, event_type, payload) VALUES(%s, %s, %s)",
                (sid, etype, json.dumps(payload or {}))
            )
    except Exception as e:
        log_error("services.analytics.log_event", e)


def log_token_usage(student_id, call_type, model, usage):
    if not config.DB_URL or not usage:
        return
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO token_usage
                   (student_id, call_type, model, input_tokens, output_tokens,
                    cache_creation_input_tokens, cache_read_input_tokens)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (student_id, call_type, model,
                 getattr(usage, "input_tokens", 0) or 0,
                 getattr(usage, "output_tokens", 0) or 0,
                 getattr(usage, "cache_creation_input_tokens", 0) or 0,
                 getattr(usage, "cache_read_input_tokens", 0) or 0),
            )
    except Exception as e:
        log_error("services.analytics.log_token_usage", e)


def _get_token_usage_by_student(cur):
    cur.execute("""
        SELECT student_id, model, input_tokens, output_tokens,
               cache_creation_input_tokens, cache_read_input_tokens
        FROM token_usage
    """)
    totals = {}
    for r in cur.fetchall():
        sid = r["student_id"]
        cost = estimate_cost_usd(
            r["model"], r["input_tokens"], r["output_tokens"],
            r["cache_creation_input_tokens"], r["cache_read_input_tokens"],
        )
        bucket = totals.setdefault(sid, {"tokens": 0, "cost_usd": 0.0})
        bucket["tokens"] += (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        bucket["cost_usd"] += cost
    for sid in totals:
        totals[sid]["cost_usd"] = round(totals[sid]["cost_usd"], 4)
    return totals


def safe_payload(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def parse_conversation_messages(raw):
    if isinstance(raw, str):
        parsed = safe_payload(raw)
        return parsed if isinstance(parsed, list) else []
    return raw if isinstance(raw, list) else []


def get_questions_this_month(sid):
    if not config.DB_URL:
        return 0
    try:
        with db_cursor() as cur:
            cur.execute("""SELECT COUNT(*) as n FROM events
                           WHERE student_id=%s AND event_type='question_asked'
                           AND created_at >= date_trunc('month', NOW())""", (sid,))
            return cur.fetchone()["n"]
    except Exception as e:
        log_error("services.analytics.get_questions_this_month", e); return 0


def get_wrapped_stats(sid):
    if not config.DB_URL:
        return None
    try:
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM events WHERE student_id=%s AND event_type='question_asked'", (sid,))
            total_questions = cur.fetchone()["n"]

            cur.execute("""SELECT course, COUNT(*) as n FROM documents WHERE student_id=%s
                           GROUP BY course ORDER BY n DESC""", (sid,))
            courses = [dict(r) for r in cur.fetchall()]

            cur.execute("""SELECT date_trunc('week', created_at) as wk, COUNT(*) as n
                           FROM events WHERE student_id=%s AND event_type='question_asked'
                           GROUP BY wk ORDER BY n DESC LIMIT 1""", (sid,))
            busiest = cur.fetchone()
            busiest_week = {"week_start": busiest["wk"].date().isoformat(), "count": busiest["n"]} if busiest else None

            cur.execute("""SELECT to_char(created_at, 'Day') as dow, COUNT(*) as n
                           FROM events WHERE student_id=%s AND event_type='question_asked'
                           GROUP BY dow ORDER BY n DESC LIMIT 1""", (sid,))
            top_day = cur.fetchone()
            busiest_day_of_week = top_day["dow"].strip() if top_day else None

            cur.execute("SELECT COUNT(*) as n FROM practice_questions WHERE student_id=%s AND correct_streak > 0", (sid,))
            questions_mastered = cur.fetchone()["n"]

            cur.execute("""SELECT DISTINCT created_at::date as d FROM events
                           WHERE student_id=%s AND event_type='question_asked' ORDER BY d""", (sid,))
            days = [r["d"] for r in cur.fetchall()]
            longest_streak, current_streak, prev = 0, 0, None
            for d in days:
                current_streak = current_streak + 1 if prev is not None and (d - prev).days == 1 else 1
                longest_streak = max(longest_streak, current_streak)
                prev = d

        return {
            "total_questions": total_questions,
            "courses": courses,
            "busiest_week": busiest_week,
            "busiest_day_of_week": busiest_day_of_week,
            "questions_mastered": questions_mastered,
            "longest_streak_days": longest_streak,
        }
    except Exception as e:
        log_error("services.analytics.get_wrapped_stats", e); return None


def _get_time_spent_by_student(cur):
    cur.execute("""
        SELECT student_id, event_type, created_at FROM events
        ORDER BY student_id, created_at ASC""")
    sessions_by_student = {}
    for r in cur.fetchall():
        sid = r["student_id"]
        if sid not in sessions_by_student:
            sessions_by_student[sid] = []
        if r["event_type"] in ("login", "account_created"):
            sessions_by_student[sid].append({"start": r["created_at"], "end": r["created_at"]})
        elif sessions_by_student[sid]:
            sessions_by_student[sid][-1]["end"] = r["created_at"]

    totals = {}
    for sid, sess_list in sessions_by_student.items():
        total_minutes = 0.0
        for sess in sess_list:
            mins = (sess["end"] - sess["start"]).total_seconds() / 60.0
            total_minutes += max(0.0, min(mins, 240.0))
        totals[sid] = round(total_minutes, 1)
    return totals


def get_page_time_breakdown(cur, student_id):
    """Per-page visit count and time spent for one student, used by the
    demo-session detail view (and usable for any student id, registered
    or demo -- nothing here is demo-specific). A page's "time spent" for
    a given visit is the gap until that student's NEXT event of any kind
    (another page_view, a question, a rating, ...), which is the same
    approach _get_time_spent_by_student() above uses for whole sessions,
    just applied per page_view instead of per login. Capped at 30 minutes
    per visit so an abandoned tab (no further activity at all -- the gap
    would otherwise run until whatever event comes next, possibly hours
    later, or forever for the very last event in the student's history)
    doesn't blow out the total. The very last event in the student's
    history has no "next" event to measure against, so it contributes a
    visit to the count but not to the time total -- its actual duration
    is genuinely unknown, not zero, and silently treating it as zero
    would just as silently undercount short sessions.
    """
    MAX_PAGE_MINUTES = 30.0
    cur.execute("""
        SELECT event_type, payload, created_at FROM events
        WHERE student_id=%s ORDER BY created_at ASC""", (student_id,))
    # Demo accounts carry ~48 backdated fake page_view events per session
    # (see _seed_demo) so a fresh visitor's dashboard looks populated --
    # those aren't real visits, and their old timestamps would also throw
    # off the gap-based time calculation below, so they're dropped
    # entirely rather than just excluded from the visit count.
    rows = [r for r in cur.fetchall() if not safe_payload(r["payload"]).get("seeded")]
    pages = {}
    for i, r in enumerate(rows):
        if r["event_type"] != "page_view":
            continue
        payload = safe_payload(r["payload"])
        page = payload.get("page") or "unknown"
        entry = pages.setdefault(page, {"page": page, "visits": 0, "minutes": 0.0})
        entry["visits"] += 1
        if i + 1 < len(rows):
            gap_minutes = (rows[i + 1]["created_at"] - r["created_at"]).total_seconds() / 60.0
            entry["minutes"] += max(0.0, min(gap_minutes, MAX_PAGE_MINUTES))
    result = list(pages.values())
    for entry in result:
        entry["minutes"] = round(entry["minutes"], 1)
    result.sort(key=lambda e: e["minutes"], reverse=True)
    return result


def get_demo_usage_stats(cur):
    cur.execute("""
        SELECT COUNT(*) as total_sessions,
               COALESCE(AVG(duration_seconds), 0) as avg_duration_seconds,
               COALESCE(MIN(duration_seconds), 0) as min_duration_seconds,
               COALESCE(MAX(duration_seconds), 0) as max_duration_seconds,
               COALESCE(SUM(questions_asked), 0) as total_questions_asked,
               COUNT(*) FILTER (WHERE ended_reason = 'expired') as expired_count
        FROM demo_sessions
    """)
    row = dict(cur.fetchone())
    cur.execute("SELECT COUNT(*) as n FROM students WHERE is_demo=TRUE AND is_active=TRUE")
    row["active_now"] = cur.fetchone()["n"] or 0
    row["avg_duration_seconds"] = round(float(row["avg_duration_seconds"] or 0))

    # Total uploads/tokens/cost across every demo account, mirroring what
    # the top-of-page stats row shows for real students (minus demo's).
    cur.execute("""SELECT COUNT(*) as n FROM events e JOIN students s ON s.id=e.student_id
                   WHERE e.event_type='file_uploaded' AND s.is_demo=TRUE""")
    row["total_uploads"] = cur.fetchone()["n"] or 0
    cur.execute("""
        SELECT t.model, SUM(t.input_tokens) as input_tokens, SUM(t.output_tokens) as output_tokens,
               SUM(t.cache_creation_input_tokens) as cache_creation_input_tokens,
               SUM(t.cache_read_input_tokens) as cache_read_input_tokens
        FROM token_usage t JOIN students s ON s.id = t.student_id
        WHERE s.is_demo=TRUE
        GROUP BY t.model
    """)
    total_tokens, total_cost = 0, 0.0
    for r in cur.fetchall():
        total_tokens += (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        total_cost += estimate_cost_usd(
            r["model"], r["input_tokens"], r["output_tokens"],
            r["cache_creation_input_tokens"], r["cache_read_input_tokens"],
        )
    row["total_tokens"] = total_tokens
    row["total_estimated_cost_usd"] = round(total_cost, 4)
    return row


def get_demo_session_summaries(cur):
    """Per-session detail for the Demo Usage tab, matching the same depth
    get_student_summaries() gives real students -- uploads, tokens, and
    estimated cost, not just start time/duration/questions. duration_seconds
    (already recorded to the second by log_demo_session_ended) doubles as
    "time spent" here; there's no separate login/logout gap calculation to
    do the way _get_time_spent_by_student() does for registered students,
    since a demo account only ever has the one session. Each demo_sessions
    row corresponds to exactly one student_id (start_demo() creates a
    fresh account per /demo/start), so unlike the Students table there's
    no separate "Sessions" count to show.
    """
    cur.execute("""
        SELECT id, student_id, to_char(started_at, 'Mon DD HH24:MI') as started,
               duration_seconds, questions_asked, ended_reason
        FROM demo_sessions
        ORDER BY started_at DESC
        LIMIT 100
    """)
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return rows
    sids = [r["student_id"] for r in rows if r["student_id"]]
    cur.execute("SELECT id, is_active FROM students WHERE id = ANY(%s)", (sids,))
    active_by_id = {r["id"]: r["is_active"] for r in cur.fetchall()}
    # Real uploads only -- _seed_demo never fabricates file_uploaded events
    # (only page_view/question_asked/practice_attempt/deadline_completed_
    # toggled), so this count needs no "seeded" filtering the way questions
    # and page visits do.
    cur.execute("""SELECT student_id, COUNT(*) as n FROM events
                   WHERE student_id = ANY(%s) AND event_type='file_uploaded'
                   GROUP BY student_id""", (sids,))
    uploads_by_id = {r["student_id"]: r["n"] for r in cur.fetchall()}
    token_usage = _get_token_usage_by_student(cur)
    for r in rows:
        sid = r["student_id"]
        r["uploads"] = uploads_by_id.get(sid, 0)
        r["is_active"] = bool(active_by_id.get(sid, False))
        usage = token_usage.get(sid, {"tokens": 0, "cost_usd": 0.0})
        r["total_tokens"] = usage["tokens"]
        r["estimated_cost_usd"] = usage["cost_usd"]
    return rows


def get_total_token_usage(cur):
    # Excludes demo accounts' token usage, same reasoning as
    # compute_engagement_insights above -- this feeds the page's overall
    # "Est. AI Cost" tile, meant to reflect real students. Demo's own
    # token/cost usage is surfaced separately in the Demo Usage tab.
    cur.execute("""
        SELECT t.model, SUM(t.input_tokens) as input_tokens, SUM(t.output_tokens) as output_tokens,
               SUM(t.cache_creation_input_tokens) as cache_creation_input_tokens,
               SUM(t.cache_read_input_tokens) as cache_read_input_tokens
        FROM token_usage t JOIN students s ON s.id = t.student_id
        WHERE s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY t.model
    """, (_ADMIN_EMAILS,))
    total_tokens = 0
    total_cost = 0.0
    for r in cur.fetchall():
        total_tokens += (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        total_cost += estimate_cost_usd(
            r["model"], r["input_tokens"], r["output_tokens"],
            r["cache_creation_input_tokens"], r["cache_read_input_tokens"],
        )
    return {"total_tokens": total_tokens, "total_estimated_cost_usd": round(total_cost, 4)}


def get_student_summaries(cur):
    cur.execute("""
        WITH event_counts AS (
            SELECT student_id,
                   COUNT(*) FILTER (WHERE event_type IN ('login','account_created')) as sessions,
                   COUNT(*) FILTER (WHERE event_type='question_asked') as questions,
                   COUNT(*) FILTER (WHERE event_type='file_uploaded') as uploads
            FROM events GROUP BY student_id
        ),
        doc_counts AS (
            SELECT student_id, COUNT(*) as docs
            FROM documents WHERE student_id IS NOT NULL GROUP BY student_id
        )
        SELECT s.id, s.first_name, s.last_name, s.email, s.classification, s.major,
               COALESCE(NULLIF(s.university,''), 'Not set') as university,
               COALESCE(s.first_generation, FALSE) as first_generation,
               to_char(s.created_at, 'Mon DD YYYY') as joined,
               s.is_active, s.account_deleted_at, s.anonymized_at, s.email_verified,
               COALESCE(ec.sessions, 0) as sessions,
               COALESCE(ec.questions, 0) as questions,
               COALESCE(ec.uploads, 0) as uploads,
               COALESCE(dc.docs, 0) as docs
        FROM students s
        LEFT JOIN event_counts ec ON ec.student_id = s.id
        LEFT JOIN doc_counts dc ON dc.student_id = s.id
        WHERE s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        ORDER BY s.created_at DESC
    """, (_ADMIN_EMAILS,))
    result = [dict(r) for r in cur.fetchall()]
    for r in result:
        r["account_deleted_at"] = r["account_deleted_at"].isoformat() if r["account_deleted_at"] else None
        r["anonymized_at"] = r["anonymized_at"].isoformat() if r["anonymized_at"] else None
    time_spent = _get_time_spent_by_student(cur)
    token_usage = _get_token_usage_by_student(cur)
    for r in result:
        r["time_spent_minutes"] = time_spent.get(r["id"], 0)
        usage = token_usage.get(r["id"], {"tokens": 0, "cost_usd": 0.0})
        r["total_tokens"] = usage["tokens"]
        r["estimated_cost_usd"] = usage["cost_usd"]
    return result


def compute_engagement_insights(cur):
    """Everything here is meant to describe real enrolled students, the
    same way get_student_summaries() and the Students tab do -- demo
    visitors have their own dedicated Demo Usage view (see
    get_demo_usage_stats/get_demo_session_summaries) with their own
    numbers, seeded backstory included. Before demo accounts stayed
    around indefinitely (see services/demo.py's end_demo_session), a
    demo row was usually gone again within hours, so most of the queries
    below could get away without an explicit is_demo filter -- deletion
    was quietly doing that job for them. Now that nothing is deleted,
    every one of them needs `s.is_demo IS NOT TRUE` explicitly, or the
    growing pile of demo accounts (each carrying ~24 fake questions and
    ~48 fake page views from _seed_demo, on top of whatever the visitor
    actually did) silently swamps these "real student" numbers -- which
    is exactly what made By University show more students than are
    actually enrolled.

    Every query also excludes `lower(s.email) != ALL(_ADMIN_EMAILS)` --
    an admin/TA account is a completely ordinary students row (there's no
    is_admin column), so without this exclusion whoever built/tested WINK
    counts as a "real student" too, and with a small pilot cohort their
    own dev/testing activity (often the single oldest account, with the
    most logins, questions, and token usage) can dominate an average like
    avg_minutes_to_first_question outright.
    """
    out = {}

    cur.execute("""
        SELECT COALESCE(NULLIF(s.university,''), 'Not set') as university,
               COUNT(DISTINCT s.id) as students,
               COUNT(*) FILTER (WHERE e.event_type IN ('login','account_created')) as sessions,
               COUNT(*) FILTER (WHERE e.event_type='question_asked') as questions,
               COUNT(*) FILTER (WHERE e.event_type='file_uploaded') as uploads
        FROM students s LEFT JOIN events e ON e.student_id = s.id
        WHERE s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY 1 ORDER BY students DESC""", (_ADMIN_EMAILS,))
    out["by_university"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT e.student_id, e.event_type, e.created_at, COALESCE(NULLIF(s.university,''),'Not set') as university
        FROM events e JOIN students s ON s.id = e.student_id
        WHERE s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        ORDER BY e.student_id, e.created_at ASC""", (_ADMIN_EMAILS,))
    rows = cur.fetchall()
    sessions_by_student = {}
    cur_session = None
    for r in rows:
        sid = r["student_id"]
        if sid not in sessions_by_student:
            sessions_by_student[sid] = []
        if r["event_type"] in ("login", "account_created"):
            cur_session = {"university": r["university"], "start": r["created_at"], "end": r["created_at"]}
            sessions_by_student[sid].append(cur_session)
        elif sessions_by_student[sid]:
            sessions_by_student[sid][-1]["end"] = r["created_at"]

    all_durations = []
    durations_by_university = {}
    for sid, sess_list in sessions_by_student.items():
        for sess in sess_list:
            mins = (sess["end"] - sess["start"]).total_seconds() / 60.0
            mins = max(0.0, min(mins, 240.0))  
            all_durations.append(mins)
            durations_by_university.setdefault(sess["university"], []).append(mins)

    out["avg_session_minutes"] = round(sum(all_durations) / len(all_durations), 1) if all_durations else 0
    out["avg_session_minutes_by_university"] = {
        u: round(sum(v) / len(v), 1) for u, v in durations_by_university.items()
    }

    cur.execute("""
        SELECT e.student_id, COUNT(DISTINCT date_trunc('week', e.created_at)) as weeks
        FROM events e JOIN students s ON s.id = e.student_id
        WHERE s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY e.student_id""", (_ADMIN_EMAILS,))
    week_rows = cur.fetchall()
    total_active = len(week_rows)
    returning = sum(1 for r in week_rows if r["weeks"] >= 2)
    out["retention_pct"] = round(returning / total_active * 100, 1) if total_active else 0

    cur.execute("""
        SELECT s.created_at as joined, MIN(e.created_at) as first_q
        FROM students s JOIN events e ON e.student_id = s.id AND e.event_type = 'question_asked'
        WHERE s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY s.id, s.created_at""", (_ADMIN_EMAILS,))
    gaps = [(r["first_q"] - r["joined"]).total_seconds() / 60.0 for r in cur.fetchall()]
    gaps = [g for g in gaps if g >= 0]
    out["avg_minutes_to_first_question"] = round(sum(gaps) / len(gaps), 1) if gaps else None

    cur.execute("""
        SELECT EXTRACT(DOW FROM e.created_at)::int as dow, EXTRACT(HOUR FROM e.created_at)::int as hour, COUNT(*) as n
        FROM events e JOIN students s ON s.id = e.student_id
        WHERE e.event_type='question_asked' AND s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY 1,2""", (_ADMIN_EMAILS,))
    grid = [[0]*24 for _ in range(7)]
    for r in cur.fetchall():
        grid[r["dow"]][r["hour"]] = r["n"]
    out["usage_heatmap"] = grid

    cur.execute("""
        SELECT d.due_date, COUNT(*) as n FROM deadlines d JOIN students s ON s.id = d.student_id
        WHERE d.due_date IS NOT NULL AND s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY d.due_date""", (_ADMIN_EMAILS,))
    due_by_date = {r["due_date"]: r["n"] for r in cur.fetchall()}
    cur.execute("""
        SELECT DATE(e.created_at) as d, COUNT(*) as n FROM events e JOIN students s ON s.id = e.student_id
        WHERE e.event_type='question_asked' AND s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY DATE(e.created_at)""", (_ADMIN_EMAILS,))
    q_by_date = {r["d"]: r["n"] for r in cur.fetchall()}
    spikes = []
    for due_date, n_due in due_by_date.items():
        same_day = q_by_date.get(due_date, 0)
        prior_3 = sum(q_by_date.get(due_date - timedelta(days=k), 0) for k in range(1, 4))
        spikes.append({
            "due_date": due_date.isoformat(), "deadlines_due": n_due,
            "questions_same_day": same_day, "questions_prior_3_days": prior_3
        })
    spikes.sort(key=lambda x: x["due_date"])
    out["deadline_spikes"] = spikes[-30:]  

    cur.execute("""
        SELECT e.event_type, COUNT(*) as n FROM events e JOIN students s ON s.id = e.student_id
        WHERE e.event_type IN ('file_uploaded','temp_file_used','global_file_uploaded')
          AND s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY e.event_type""", (_ADMIN_EMAILS,))
    mix = {r["event_type"]: r["n"] for r in cur.fetchall()}
    out["upload_mix"] = {
        "permanent": mix.get("file_uploaded", 0),
        "temporary": mix.get("temp_file_used", 0),
        "global": mix.get("global_file_uploaded", 0),
    }

    cur.execute("""
        SELECT id, orig_name, university, course as label,
               to_char(uploaded_at,'Mon DD YYYY') as uploaded_at
        FROM documents
        WHERE student_id IS NULL AND uploaded_at < NOW() - INTERVAL '90 days'
        ORDER BY uploaded_at ASC LIMIT 20""")
    out["stale_global_docs"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT
          COUNT(*) FILTER (
            WHERE EXISTS (
              SELECT 1 FROM documents gd
              WHERE gd.student_id IS NULL
                AND lower(gd.university) = lower(st.university)
                AND gd.uploaded_at <= e.created_at
            )
          ) as with_docs,
          COUNT(*) as total
        FROM events e JOIN students st ON st.id = e.student_id
        WHERE e.event_type = 'question_asked' AND st.is_demo IS NOT TRUE
          AND lower(st.email) != ALL(%s)""", (_ADMIN_EMAILS,))
    row = cur.fetchone()
    out["general_doc_availability_pct"] = (
        round(row["with_docs"] / row["total"] * 100, 1) if row and row["total"] else 0
    )

    cur.execute("""
        SELECT (e.payload::json->>'rating') as rating, COUNT(*) as n
        FROM events e JOIN students s ON s.id = e.student_id
        WHERE e.event_type='answer_feedback' AND s.is_demo IS NOT TRUE AND lower(s.email) != ALL(%s)
        GROUP BY (e.payload::json->>'rating')""", (_ADMIN_EMAILS,))
    counts = {r["rating"]: r["n"] for r in cur.fetchall()}
    up, down = counts.get("up", 0), counts.get("down", 0)
    out["answer_feedback"] = {
        "up": up, "down": down,
        "positive_pct": round(up / (up + down) * 100, 1) if (up + down) else None,
    }

    cur.execute("""
        SELECT (e.payload::json->>'q') as question, COUNT(*) as n, COUNT(DISTINCT e.student_id) as n_students
        FROM events e JOIN students s ON s.id = e.student_id
        WHERE e.event_type = 'question_asked'
          AND s.is_demo IS NOT TRUE
          AND lower(s.email) != ALL(%s)
          AND e.created_at >= NOW() - INTERVAL '7 days'
          AND length(e.payload::json->>'q') > 8
        GROUP BY (e.payload::json->>'q')
        HAVING COUNT(DISTINCT e.student_id) >= 2
        ORDER BY n_students DESC, n DESC
        LIMIT 15
    """, (_ADMIN_EMAILS,))
    out["common_questions"] = [dict(r) for r in cur.fetchall()]

    return out


def _anonymize_student_sql(cur, student_id):
    """The actual SQL work of anonymize_student_record() below, using a
    cursor the caller already has open — no connection handling, no
    commit. Exists so a caller that needs anonymization to happen
    atomically alongside something else in the same transaction (see
    auth.py's delete_account(), which must not commit "account deleted"
    without anonymization actually having happened too) can do so,
    without duplicating this SQL.

    Returns the new opaque label on success, or None if the student
    wasn't found or was already anonymized — same contract as the
    public function below."""
    cur.execute("SELECT id, email, anonymized_at FROM students WHERE id=%s", (student_id,))
    target = cur.fetchone()
    if not target or target["anonymized_at"]:
        return None
    original_email = target["email"]
    code = secrets.token_hex(6)
    cur.execute("""UPDATE students SET first_name=%s, last_name=%s, email=%s,
                   password_hash=%s, anonymized_at=NOW() WHERE id=%s""",
                ("Anonymized", f"Participant-{code}", f"anon-{code}@anonymized.wink",
                 generate_password_hash(secrets.token_hex(32)), student_id))
    # Scrub any email address left behind in this student's own event
    # payloads (from before email-in-events was stopped).
    cur.execute("""UPDATE events SET payload = (payload::jsonb - 'email')::text
                   WHERE student_id=%s AND payload::jsonb ? 'email'""", (student_id,))
    # email_events (SES bounce/complaint log) has no foreign key to the
    # students table — it's keyed by email address alone — so it has to be
    # scrubbed by matching the original address directly, using the value
    # captured above before it was overwritten.
    if original_email:
        cur.execute("""UPDATE email_events SET email=%s WHERE lower(email)=lower(%s)""",
                    (f"anon-{code}@anonymized.wink", original_email))
        cur.execute("""UPDATE email_suppressions SET email=%s WHERE lower(email)=lower(%s)""",
                    (f"anon-{code}@anonymized.wink", original_email))
    return f"Participant-{code}"


def anonymize_student_record(student_id):
    """Shared by both the admin-triggered anonymization action and a
    student's own account deletion — replaces identifying fields with an
    opaque, untraceable label and scrubs the student's original email
    address from every place WINK logs it independently of the students
    row itself.

    Irreversible: the original name/email are overwritten, not stored
    anywhere else. Login is disabled (password hash randomized) since the
    account can no longer be meaningfully identified by its owner anyway.

    Scope, to be upfront about it: this scrubs the student row, WINK's own
    system-generated event payloads, and email_events (SES bounce/complaint
    records, which are keyed by email address independently of the student
    row and would otherwise keep the original address forever). It does
    NOT search conversation text or document content for a name a student
    may have typed themselves (e.g. "hi, I'm Jane") — that content stays
    as uploaded/written, since altering it would corrupt the research
    record it exists to preserve.

    This function manages its own connection/commit — fine for the
    admin-triggered action, which has nothing else that needs to succeed
    or fail together with it. If you need anonymization to happen
    atomically alongside something else (see delete_account() in
    auth.py), use _anonymize_student_sql() directly on your own cursor
    instead of this wrapper.

    Returns the new opaque label (e.g. "Participant-a1b2c3") on success,
    or None if the student wasn't found or was already anonymized.
    """
    if not config.DB_URL:
        return None
    with db_cursor(commit=True) as cur:
        return _anonymize_student_sql(cur, student_id)
