"""Event logging and the admin analytics dashboard's data queries."""
import json
import traceback
from datetime import timedelta

from .. import config
from ..extensions import get_db


def log_event(sid, etype, payload=None):
    """Log every user action to the events table."""
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
        print(f"log_event ERROR: {e}")
        traceback.print_exc()


def safe_payload(raw):
    """Safely parse a payload value regardless of whether it's str, dict, or None."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def parse_conversation_messages(raw):
    """Conversations.messages comes back from Postgres as either a JSON
    string or (depending on driver/column state) an already-parsed list —
    this normalizes either into a plain list, defaulting to [] for
    anything else. Used everywhere a saved conversation's message list is
    read (listing, loading, exporting, sharing, appending a new turn)."""
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
        print(f"get_questions_this_month error: {e}"); return 0


def get_student_summaries(cur):
    """Per-student sessions/questions/uploads/docs counts for the admin
    dashboard. Written as two grouped aggregates (one pass over `events`,
    one over `documents`) joined once to `students`, instead of four
    correlated subqueries evaluated per student row — at "hundreds of
    students, thousands of events" scale, that's O(events + documents +
    students) rather than O(students) separate index lookups per column,
    which matters once this runs across many schools instead of one
    classroom's worth of students."""
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
               to_char(s.created_at, 'Mon DD YYYY') as joined,
               COALESCE(ec.sessions, 0) as sessions,
               COALESCE(ec.questions, 0) as questions,
               COALESCE(ec.uploads, 0) as uploads,
               COALESCE(dc.docs, 0) as docs
        FROM students s
        LEFT JOIN event_counts ec ON ec.student_id = s.id
        LEFT JOIN doc_counts dc ON dc.student_id = s.id
        ORDER BY s.created_at DESC
    """)
    return [dict(r) for r in cur.fetchall()]


def compute_engagement_insights(cur):
    """Everything beyond the basic counts already in /analytics-data-full:
    per-university breakdown, session duration, retention, time-to-first-question,
    peak usage heatmap, deadline-driven spikes, upload mix, stale docs, and a
    rough 'general reference material was available' rate. All computed from
    the existing students/events/documents/deadlines tables via grouped
    aggregate queries — no per-row correlated subqueries, no schema or
    frontend instrumentation changes required."""
    out = {}

    # ── Per-university breakdown ──
    cur.execute("""
        SELECT COALESCE(NULLIF(s.university,''), 'Not set') as university,
               COUNT(DISTINCT s.id) as students,
               COUNT(*) FILTER (WHERE e.event_type IN ('login','account_created')) as sessions,
               COUNT(*) FILTER (WHERE e.event_type='question_asked') as questions,
               COUNT(*) FILTER (WHERE e.event_type='file_uploaded') as uploads
        FROM students s LEFT JOIN events e ON e.student_id = s.id
        GROUP BY 1 ORDER BY students DESC""")
    out["by_university"] = [dict(r) for r in cur.fetchall()]

    # ── Session duration (derived from event timestamps — a new session
    #    starts at each login/account_created; its duration is the gap to the
    #    last event before the next session starts) ──
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
            mins = max(0.0, min(mins, 240.0))  # ignore/cap runaway gaps
            all_durations.append(mins)
            durations_by_university.setdefault(sess["university"], []).append(mins)

    out["avg_session_minutes"] = round(sum(all_durations) / len(all_durations), 1) if all_durations else 0
    out["avg_session_minutes_by_university"] = {
        u: round(sum(v) / len(v), 1) for u, v in durations_by_university.items()
    }

    # ── Retention: % of students active across 2+ distinct weeks ──
    cur.execute("""
        SELECT student_id, COUNT(DISTINCT date_trunc('week', created_at)) as weeks
        FROM events GROUP BY student_id""")
    week_rows = cur.fetchall()
    total_active = len(week_rows)
    returning = sum(1 for r in week_rows if r["weeks"] >= 2)
    out["retention_pct"] = round(returning / total_active * 100, 1) if total_active else 0

    # ── Time-to-first-question ──
    cur.execute("""
        SELECT s.created_at as joined, MIN(e.created_at) as first_q
        FROM students s JOIN events e ON e.student_id = s.id AND e.event_type = 'question_asked'
        GROUP BY s.id, s.created_at""")
    gaps = [(r["first_q"] - r["joined"]).total_seconds() / 60.0 for r in cur.fetchall()]
    gaps = [g for g in gaps if g >= 0]
    out["avg_minutes_to_first_question"] = round(sum(gaps) / len(gaps), 1) if gaps else None

    # ── Peak usage heatmap (questions asked, by day-of-week x hour) ──
    cur.execute("""
        SELECT EXTRACT(DOW FROM created_at)::int as dow, EXTRACT(HOUR FROM created_at)::int as hour, COUNT(*) as n
        FROM events WHERE event_type='question_asked' GROUP BY 1,2""")
    grid = [[0]*24 for _ in range(7)]
    for r in cur.fetchall():
        grid[r["dow"]][r["hour"]] = r["n"]
    out["usage_heatmap"] = grid

    # ── Deadline-driven spikes: questions asked on/around days with deadlines due ──
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
    out["deadline_spikes"] = spikes[-30:]  # most recent/upcoming 30 dates with deadlines

    # ── Upload mix: permanent vs temporary vs admin/global ──
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

    # ── Stale general-reference documents (untouched 90+ days) ──
    cur.execute("""
        SELECT id, orig_name, university, course as label,
               to_char(uploaded_at,'Mon DD YYYY') as uploaded_at
        FROM documents
        WHERE student_id IS NULL AND uploaded_at < NOW() - INTERVAL '90 days'
        ORDER BY uploaded_at ASC LIMIT 20""")
    out["stale_global_docs"] = [dict(r) for r in cur.fetchall()]

    # ── Rough "general reference material was available" rate: for each
    #    question, did that student's university have at least one global
    #    doc uploaded by that point? This is availability, not confirmed
    #    usage — we don't log whether the model's answer actually drew on it. ──
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

    # ── Answer feedback (thumbs up/down) ──
    # payload is stored as a JSON string in a TEXT column (see log_event) —
    # cast it inline rather than pulling every row into Python, consistent
    # with how every other aggregate here is computed.
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

    return out
