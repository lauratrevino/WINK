import json
import secrets
from datetime import timedelta

from werkzeug.security import generate_password_hash

from .. import config
from ..errors import log_error
from ..extensions import get_db
from .pricing import estimate_cost_usd


def log_event(sid, etype, payload=None):
    if not config.DB_URL:
        return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO events(student_id, event_type, payload) VALUES(%s, %s, %s)",
            (sid, etype, json.dumps(payload or {}))
        )
        conn.commit(); cur.close()
    except Exception as e:
        log_error("services.analytics.log_event", e)


def log_token_usage(student_id, call_type, model, usage):
    if not config.DB_URL or not usage:
        return
    try:
        conn = get_db(); cur = conn.cursor()
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
        conn.commit(); cur.close()
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT COUNT(*) as n FROM events
                       WHERE student_id=%s AND event_type='question_asked'
                       AND created_at >= date_trunc('month', NOW())""", (sid,))
        n = cur.fetchone()["n"]; cur.close()
        return n
    except Exception as e:
        log_error("services.analytics.get_questions_this_month", e); return 0


def get_wrapped_stats(sid):
    if not config.DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()

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

        cur.close()
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
    cur.execute("SELECT COUNT(*) as n FROM students WHERE is_demo=TRUE")
    row["active_now"] = cur.fetchone()["n"] or 0
    row["avg_duration_seconds"] = round(float(row["avg_duration_seconds"] or 0))
    return row


def get_total_token_usage(cur):
    cur.execute("""
        SELECT model, SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens,
               SUM(cache_creation_input_tokens) as cache_creation_input_tokens,
               SUM(cache_read_input_tokens) as cache_read_input_tokens
        FROM token_usage GROUP BY model
    """)
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
               to_char(s.created_at, 'Mon DD YYYY') as joined,
               s.is_active, s.account_deleted_at, s.anonymized_at,
               COALESCE(ec.sessions, 0) as sessions,
               COALESCE(ec.questions, 0) as questions,
               COALESCE(ec.uploads, 0) as uploads,
               COALESCE(dc.docs, 0) as docs
        FROM students s
        LEFT JOIN event_counts ec ON ec.student_id = s.id
        LEFT JOIN doc_counts dc ON dc.student_id = s.id
        WHERE s.is_demo IS NOT TRUE
        ORDER BY s.created_at DESC
    """)
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
    out = {}

    cur.execute("""
        SELECT COALESCE(NULLIF(s.university,''), 'Not set') as university,
               COUNT(DISTINCT s.id) as students,
               COUNT(*) FILTER (WHERE e.event_type IN ('login','account_created')) as sessions,
               COUNT(*) FILTER (WHERE e.event_type='question_asked') as questions,
               COUNT(*) FILTER (WHERE e.event_type='file_uploaded') as uploads
        FROM students s LEFT JOIN events e ON e.student_id = s.id
        GROUP BY 1 ORDER BY students DESC""")
    out["by_university"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT e.student_id, e.event_type, e.created_at, COALESCE(NULLIF(s.university,''),'Not set') as university
        FROM events e JOIN students s ON s.id = e.student_id
        ORDER BY e.student_id, e.created_at ASC""")
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
        SELECT student_id, COUNT(DISTINCT date_trunc('week', created_at)) as weeks
        FROM events GROUP BY student_id""")
    week_rows = cur.fetchall()
    total_active = len(week_rows)
    returning = sum(1 for r in week_rows if r["weeks"] >= 2)
    out["retention_pct"] = round(returning / total_active * 100, 1) if total_active else 0

    cur.execute("""
        SELECT s.created_at as joined, MIN(e.created_at) as first_q
        FROM students s JOIN events e ON e.student_id = s.id AND e.event_type = 'question_asked'
        GROUP BY s.id, s.created_at""")
    gaps = [(r["first_q"] - r["joined"]).total_seconds() / 60.0 for r in cur.fetchall()]
    gaps = [g for g in gaps if g >= 0]
    out["avg_minutes_to_first_question"] = round(sum(gaps) / len(gaps), 1) if gaps else None

    cur.execute("""
        SELECT EXTRACT(DOW FROM created_at)::int as dow, EXTRACT(HOUR FROM created_at)::int as hour, COUNT(*) as n
        FROM events WHERE event_type='question_asked' GROUP BY 1,2""")
    grid = [[0]*24 for _ in range(7)]
    for r in cur.fetchall():
        grid[r["dow"]][r["hour"]] = r["n"]
    out["usage_heatmap"] = grid

    cur.execute("SELECT due_date, COUNT(*) as n FROM deadlines WHERE due_date IS NOT NULL GROUP BY due_date")
    due_by_date = {r["due_date"]: r["n"] for r in cur.fetchall()}
    cur.execute("SELECT DATE(created_at) as d, COUNT(*) as n FROM events WHERE event_type='question_asked' GROUP BY DATE(created_at)")
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
        SELECT event_type, COUNT(*) as n FROM events
        WHERE event_type IN ('file_uploaded','temp_file_used','global_file_uploaded')
        GROUP BY event_type""")
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
        WHERE e.event_type = 'question_asked'""")
    row = cur.fetchone()
    out["general_doc_availability_pct"] = (
        round(row["with_docs"] / row["total"] * 100, 1) if row and row["total"] else 0
    )

    cur.execute("""
        SELECT (payload::json->>'rating') as rating, COUNT(*) as n
        FROM events WHERE event_type='answer_feedback'
        GROUP BY (payload::json->>'rating')""")
    counts = {r["rating"]: r["n"] for r in cur.fetchall()}
    up, down = counts.get("up", 0), counts.get("down", 0)
    out["answer_feedback"] = {
        "up": up, "down": down,
        "positive_pct": round(up / (up + down) * 100, 1) if (up + down) else None,
    }

    cur.execute("""
        SELECT (payload::json->>'q') as question, COUNT(*) as n, COUNT(DISTINCT student_id) as n_students
        FROM events
        WHERE event_type = 'question_asked'
          AND created_at >= NOW() - INTERVAL '7 days'
          AND length(payload::json->>'q') > 8
        GROUP BY (payload::json->>'q')
        HAVING COUNT(DISTINCT student_id) >= 2
        ORDER BY n_students DESC, n DESC
        LIMIT 15
    """)
    out["common_questions"] = [dict(r) for r in cur.fetchall()]

    return out


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

    Returns the new opaque label (e.g. "Participant-a1b2c3") on success,
    or None if the student wasn't found or was already anonymized.
    """
    if not config.DB_URL:
        return None
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, email, anonymized_at FROM students WHERE id=%s", (student_id,))
    target = cur.fetchone()
    if not target or target["anonymized_at"]:
        cur.close()
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
    conn.commit(); cur.close()
    return f"Participant-{code}"
