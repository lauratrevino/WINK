"""Analytics > Insights additions (students to check on, feature adoption,
question topics, answers to review, practice results, cost outlook) and
the per-student research export. Everything here covers real (non-demo)
students only."""
import re
from datetime import datetime

from .. import config
from .analytics import (_all_page_time, _get_time_spent_by_student, _get_token_usage_by_student,
                        safe_payload, PAGE_LABELS)

INACTIVE_DAYS = 7

# Keyword rules for question topics. Transparent and repeatable (the same
# question always lands in the same topic), which matters for research;
# first matching topic wins, checked in this order.
TOPIC_RULES = [
    ("Campus resources", r"\b(office|advis|tutor|counsel|financial aid|library|parking|registrar|"
                         r"cass|writing center|career|health|where is|located|hours|phone|email|contact|map)\b"),
    ("Deadlines & schedule", r"\b(due|deadline|when is|what'?s due|this week|next week|tomorrow|today|"
                             r"schedule|calendar|exam date|midterm|final exam|late)\b"),
    ("Grades", r"\b(grade|gpa|score|points|percent|weight|pass|fail|what do i need)\b"),
    ("Assignment help", r"\b(assignment|project|deliverable|essay|paper|report|homework|lab|slides?|"
                        r"presentation|write|create|draft|outline|gantt|wbs|rubric|submission|check my)\b"),
    ("Understanding concepts", r"\b(explain|what is|what are|how does|how do|why|define|definition|"
                               r"example|mean|difference|understand|concept)\b"),
    ("Study help", r"\b(study|quiz|practice|review|flashcard|prepare|test|summar)\b"),
]
_TOPIC_RES = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in TOPIC_RULES]


def classify_question(q):
    for name, rx in _TOPIC_RES:
        if rx.search(q or ""):
            return name
    return "Other"


def _real_students(cur):
    cur.execute("""SELECT id, first_name, last_name, email, classification, major, university,
                          first_generation, research_consent, created_at, is_active
                   FROM students WHERE is_demo IS NOT TRUE AND anonymized_at IS NULL
                   ORDER BY id""")
    # Admin accounts (you) aren't study participants, so they're left out
    # of check-ins, adoption numbers, and the research export.
    return [dict(r) for r in cur.fetchall()
            if (r["email"] or "").lower() not in config.ADMIN_EMAILS]


def get_deadline_followthrough(cur):
    """{student_id: {past_due, completed, on_time, overdue_open}} for course
    deadlines (not personal items) whose due date has passed."""
    cur.execute("""SELECT d.student_id,
                          COUNT(*) AS past_due,
                          COUNT(*) FILTER (WHERE d.completed) AS completed,
                          COUNT(*) FILTER (WHERE d.completed AND d.completed_at IS NOT NULL
                                           AND d.completed_at::date <= d.due_date) AS on_time,
                          COUNT(*) FILTER (WHERE NOT d.completed) AS overdue_open
                   FROM deadlines d JOIN students s ON s.id = d.student_id
                   WHERE s.is_demo IS NOT TRUE AND d.is_personal IS NOT TRUE
                     AND d.due_date IS NOT NULL AND d.due_date < CURRENT_DATE
                   GROUP BY d.student_id""")
    return {r["student_id"]: dict(r) for r in cur.fetchall()}


def get_practice_by_student(cur):
    cur.execute("""SELECT e.student_id, e.payload FROM events e JOIN students s ON s.id = e.student_id
                   WHERE s.is_demo IS NOT TRUE AND e.event_type = 'practice_attempt'""")
    out = {}
    for r in cur.fetchall():
        p = safe_payload(r["payload"])
        if p.get("seeded"):
            continue
        b = out.setdefault(r["student_id"], {"attempts": 0, "correct": 0})
        b["attempts"] += 1
        b["correct"] += 1 if p.get("correct") else 0
    return out


def get_insights_extra(cur):
    students = _real_students(cur)
    ids = [s["id"] for s in students]
    now = datetime.utcnow()

    # ---- activity per student (last active, features used), aggregated in SQL ----
    cur.execute("""SELECT e.student_id,
                          MAX(e.created_at) AS last_active,
                          BOOL_OR(e.event_type = 'file_uploaded') AS upload,
                          BOOL_OR(e.event_type = 'question_asked') AS ask,
                          BOOL_OR(e.event_type IN ('practice_attempt','practice_questions_generated',
                                                   'practice_summary_generated','study_plan_generated')
                                  OR (e.event_type = 'page_view' AND e.payload LIKE '%%"practice"%%')) AS practice,
                          BOOL_OR(e.event_type = 'grading_weights_extracted'
                                  OR (e.event_type = 'page_view' AND e.payload LIKE '%%"grades"%%')) AS grades,
                          BOOL_OR(e.event_type IN ('deadline_completed_toggled','deadline_status_changed','personal_item_added')
                                  OR (e.event_type = 'page_view' AND e.payload LIKE '%%"calendar"%%')) AS calendar
                   FROM events e JOIN students s ON s.id = e.student_id
                   WHERE s.is_demo IS NOT TRUE
                   GROUP BY e.student_id""")
    last_active, used = {}, {}
    for r in cur.fetchall():
        last_active[r["student_id"]] = r["last_active"]
        used[r["student_id"]] = {k for k in ("upload", "ask", "practice", "grades", "calendar") if r[k]}
    cur.execute("SELECT DISTINCT student_id FROM documents WHERE student_id IS NOT NULL")
    for r in cur.fetchall():
        used.setdefault(r["student_id"], set()).add("upload")

    follow = get_deadline_followthrough(cur)

    # ---- 1. students to check on ----
    check_on = []
    for s in students:
        if not s.get("is_active"):
            continue
        la = last_active.get(s["id"])
        days = (now - la).days if la else (now - s["created_at"]).days
        overdue = (follow.get(s["id"]) or {}).get("overdue_open", 0)
        reasons = []
        if days >= INACTIVE_DAYS:
            reasons.append(f"No activity in {days} days" if la else f"Never used WINK ({days} days since joining)")
        if overdue:
            reasons.append(f"{overdue} past-due deadline{'s' if overdue != 1 else ''} not checked off")
        if reasons:
            check_on.append({"id": s["id"], "name": f"{s['first_name']} {s['last_name']}", "email": s["email"],
                             "last_active": la.strftime("%b %d") if la else "Never",
                             "days_inactive": days, "overdue_open": overdue, "reasons": reasons})
    check_on.sort(key=lambda x: (-x["days_inactive"], -x["overdue_open"]))

    # ---- 3. feature adoption ----
    n = len(students)
    def count(feat):
        return sum(1 for s in students if feat in used.get(s["id"], set()))
    adoption = [
        {"step": "Registered", "n": n},
        {"step": "Uploaded a document", "n": count("upload")},
        {"step": "Asked WINK a question", "n": count("ask")},
        {"step": "Opened Calendar", "n": count("calendar")},
        {"step": "Opened Practice", "n": count("practice")},
        {"step": "Opened Grades", "n": count("grades")},
    ]
    for a in adoption:
        a["pct"] = round(100 * a["n"] / n) if n else 0

    # ---- 4. question topics ----
    cur.execute("""SELECT a.question FROM answer_logs a JOIN students s ON s.id = a.student_id
                   WHERE s.is_demo IS NOT TRUE""")
    topic_counts = {}
    total_q = 0
    for r in cur.fetchall():
        t = classify_question(r["question"])
        topic_counts[t] = topic_counts.get(t, 0) + 1
        total_q += 1
    topics = sorted(({"topic": k, "n": v, "pct": round(100 * v / total_q) if total_q else 0}
                     for k, v in topic_counts.items()), key=lambda x: -x["n"])

    # ---- 5. answers to review (thumbs down) ----
    cur.execute("""SELECT a.id, a.question, a.answer_text, a.model, a.faculty_rating,
                          to_char(a.created_at, 'Mon DD, YYYY') AS ts, s.first_name, s.last_name
                   FROM answer_logs a JOIN students s ON s.id = a.student_id
                   WHERE s.is_demo IS NOT TRUE AND a.student_feedback = 'down'
                   ORDER BY a.created_at DESC LIMIT 100""")
    review = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT COUNT(*) FILTER (WHERE student_feedback = 'up') AS up,
                          COUNT(*) FILTER (WHERE student_feedback = 'down') AS down
                   FROM answer_logs a JOIN students s ON s.id = a.student_id WHERE s.is_demo IS NOT TRUE""")
    fb = dict(cur.fetchone())

    # ---- 7. practice results by course ----
    cur.execute("""SELECT e.payload, e.created_at FROM events e JOIN students s ON s.id = e.student_id
                   WHERE s.is_demo IS NOT TRUE AND e.event_type = 'practice_attempt'
                   ORDER BY e.created_at ASC""")
    by_course = {}
    for r in cur.fetchall():
        p = safe_payload(r["payload"])
        if p.get("seeded"):
            continue
        c = p.get("course") or "Unknown course"
        wk = r["created_at"].strftime("%Y-W%W")
        b = by_course.setdefault(c, {"course": c, "attempts": 0, "correct": 0, "weeks": {}})
        b["attempts"] += 1
        b["correct"] += 1 if p.get("correct") else 0
        w = b["weeks"].setdefault(wk, [0, 0])
        w[0] += 1
        w[1] += 1 if p.get("correct") else 0
    practice = []
    for b in sorted(by_course.values(), key=lambda x: -x["attempts"]):
        weeks = sorted(b["weeks"].items())
        first = weeks[0][1] if weeks else [0, 0]
        last = weeks[-1][1] if weeks else [0, 0]
        practice.append({
            "course": b["course"], "attempts": b["attempts"],
            "accuracy": round(100 * b["correct"] / b["attempts"]) if b["attempts"] else 0,
            "first_week_accuracy": round(100 * first[1] / first[0]) if first[0] else None,
            "latest_week_accuracy": round(100 * last[1] / last[0]) if last[0] else None,
            "weeks": len(weeks),
        })

    # ---- 8. cost outlook ----
    usage = _get_token_usage_by_student(cur)
    total_cost = sum(v["cost_usd"] for sid, v in usage.items() if sid in set(ids))
    users_with_cost = sum(1 for sid, v in usage.items() if sid in set(ids) and v["cost_usd"] > 0)
    cur.execute("""SELECT MIN(t.created_at) AS first FROM token_usage t JOIN students s ON s.id = t.student_id
                   WHERE s.is_demo IS NOT TRUE""")
    first_use = cur.fetchone()["first"]
    weeks = max(1.0, (now - first_use).days / 7.0) if first_use else 1.0
    per_student_week = (total_cost / users_with_cost / weeks) if users_with_cost else 0.0
    cost = {
        "total_cost": round(total_cost, 2), "students_using_ai": users_with_cost, "weeks": round(weeks, 1),
        "per_student_per_week": round(per_student_week, 4),
        "projections": [{"students": k, "per_week": round(per_student_week * k, 2),
                         "per_semester": round(per_student_week * k * 16, 2)} for k in (100, 1000, 5000)],
    }

    return {"check_on": check_on, "adoption": adoption, "topics": topics, "topic_total": total_q,
            "answers_to_review": review, "feedback_totals": fb, "practice_by_course": practice,
            "cost_outlook": cost}


def get_export_rows(cur, anonymize=True):
    """One row per real student for the engagement export (CSV). Every
    student is included; the research_consent column shows who agreed to
    research use, so declined rows can be filtered out for analysis."""
    students = _real_students(cur)
    time_spent = _get_time_spent_by_student(cur)
    usage = _get_token_usage_by_student(cur)
    follow = get_deadline_followthrough(cur)
    practice = get_practice_by_student(cur)

    cur.execute("""SELECT e.student_id,
                          COUNT(*) FILTER (WHERE e.event_type IN ('login','account_created')) AS sessions,
                          COUNT(*) FILTER (WHERE e.event_type = 'question_asked') AS questions,
                          COUNT(*) FILTER (WHERE e.event_type = 'file_uploaded') AS uploads,
                          MAX(e.created_at) AS last
                   FROM events e JOIN students s ON s.id = e.student_id
                   WHERE s.is_demo IS NOT TRUE GROUP BY e.student_id""")
    counts = {r["student_id"]: dict(r) for r in cur.fetchall()}
    page_time = _all_page_time(cur)

    page_keys = ["dashboard", "documents", "calendar", "chat", "practice", "grades", "progress", "manual"]
    out = []
    for i, s in enumerate(students, start=1):
        sid = s["id"]
        pt = page_time.get(sid) or {"totals_by_page": [], "grand_total_minutes": 0, "session_count": 0}
        page_min = {p["page"]: p["minutes"] for p in pt["totals_by_page"]}
        f = follow.get(sid, {})
        pr = practice.get(sid, {})
        c = counts.get(sid, {})
        u = usage.get(sid, {"tokens": 0, "cost_usd": 0.0})
        row = {}
        if anonymize:
            row["participant"] = f"P{i:03d}"
        else:
            row["student_id"] = sid
            row["first_name"] = s["first_name"]
            row["last_name"] = s["last_name"]
            row["email"] = s["email"]
        row.update({
            "research_consent": "yes" if s.get("research_consent") else "no",
            "classification": s.get("classification") or "",
            "major": s.get("major") or "",
            "first_generation": "yes" if s.get("first_generation") else "no",
            "university": s.get("university") or "",
            "joined": s["created_at"].strftime("%Y-%m-%d") if s.get("created_at") else "",
            "last_active": c["last"].strftime("%Y-%m-%d") if c.get("last") else "",
            "sessions": c.get("sessions", 0),
            "questions": c.get("questions", 0),
            "uploads": c.get("uploads", 0),
            "time_spent_minutes": time_spent.get(sid, 0),
            "time_on_pages_minutes": pt["grand_total_minutes"],
            "active_sessions": pt["session_count"],
        })
        for k in page_keys:
            row[f"minutes_{PAGE_LABELS.get(k, k).lower().replace(' ', '_')}"] = page_min.get(k, 0)
        row.update({
            "past_due_deadlines": f.get("past_due", 0),
            "deadlines_completed": f.get("completed", 0),
            "deadlines_on_time": f.get("on_time", 0),
            "practice_attempts": pr.get("attempts", 0),
            "practice_accuracy_pct": round(100 * pr["correct"] / pr["attempts"]) if pr.get("attempts") else "",
            "ai_tokens": u.get("tokens", 0),
            "ai_cost_usd": round(u.get("cost_usd", 0.0), 4),
        })
        out.append(row)
    return out
