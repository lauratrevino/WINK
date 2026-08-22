import calendar as _pycalendar
import secrets
from datetime import datetime, timedelta

from .. import config
from ..errors import log_error
from ..extensions import db_cursor, anthropic_client
from .analytics import log_token_usage
from .json_utils import parse_json_array, strip_json_fence



def extract_deadlines(content, today=None, student_id=None):
    if not anthropic_client or not content or not content.strip():
        return []
    # Uses the app's configured local timezone, not UTC — for hours every
    # day, UTC's "today" is already tomorrow in Mountain Time (see the note
    # on config.APP_TIMEZONE), which would shift every relative date
    # ("due next Friday") the AI extracts from a document by a day for any
    # upload happening late at night local time. None of this function's
    # callers pass an explicit `today`, so this default is what's actually
    # used on every single upload.
    from zoneinfo import ZoneInfo
    today = today or datetime.now(ZoneInfo(config.APP_TIMEZONE)).strftime("%Y-%m-%d")
    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            max_tokens=8192,
            system=(
                "Extract EVERY dated schedule entry from the document text the user "
                "provides — read the whole document, including every week/session row "
                "of any course schedule or calendar table, not just a single 'due dates' "
                "or 'assignments' column. This includes, but is not limited to:\n"
                "- Assignments, exams, quizzes, projects, and other graded deliverables\n"
                "- Recurring per-session/per-week schedule entries such as topics, "
                "themes, focus areas, discussion questions, readings, labs, or "
                "'question of the day' style entries tied to a specific class date — "
                "even when nothing is formally 'due' on that date, the topic/theme/focus "
                "itself is a schedule entry to capture, using that session's date as its "
                "due_date\n"
                "- Any other named, dated item in a course schedule, however it's labeled\n"
                "A single syllabus commonly has many such entries (dozens for a "
                "full-semester weekly schedule) — extract all of them, do not stop early "
                "or sample only a subset. If the same date has multiple distinct entries "
                "(e.g., a topic AND a deliverable on the same day), include each as its "
                "own separate object rather than merging them.\n"
                "Respond with ONLY a JSON array (no prose, no markdown fences) of objects "
                'shaped like {"title": "...", "due_date": "YYYY-MM-DD", "source_snippet": '
                "\"...\"}. title should include the schedule column's label when it isn't "
                "a plain assignment (e.g. \"Studio Focus: Cubism\", \"Question of the Day: "
                "...\", \"Historical Problem: ...\") so the student can tell what kind of "
                "entry it is. source_snippet must be the actual sentence (or short span, "
                "under 200 characters) from the document that the title/due_date were "
                "read from — never paraphrase or invent it; copy it from the text given "
                "to you. "
                f"Today's date is {today} — resolve relative or partial dates "
                "(e.g. \"March 3\" with no year, or \"next Friday\") against it. "
                "Skip only entries with no specific date you can resolve. "
                "If there are no clear dated entries, respond with []."
            ),
            messages=[{"role": "user", "content": content[:config.DEADLINE_EXTRACTION_MAX_CHARS]}],
        )
        if student_id is not None:
            log_token_usage(student_id, "deadline_extraction", config.CHAT_MODEL, resp.usage)
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        raw = strip_json_fence(raw)
        items = parse_json_array(raw)
        out = []
        for it in items if isinstance(items, list) else []:
            title = str(it.get("title", "")).strip()[:200]
            due = str(it.get("due_date", "")).strip()
            try:
                datetime.strptime(due, "%Y-%m-%d")
            except Exception:
                continue
            if title:
                out.append({
                    "title": title,
                    "due_date": due,
                    "source_snippet": str(it.get("source_snippet", "")).strip()[:200],
                })
        # A full-semester weekly schedule (topics/themes/focus entries plus
        # actual deliverables) commonly runs well past 30 items — the old cap
        # here was silently dropping later-semester entries once a syllabus
        # had more than 30 total dated items, which independently explained
        # some of the scattered "missing" deliverables alongside the
        # narrower-scope prompt issue above. 200 comfortably covers a
        # semester's worth of entries while still bounding worst-case size.
        return out[:200]
    except Exception as e:
        log_error("services.deadlines.extract_deadlines", e)
        return []


def insert_deadlines(student_id, document_id, course, items):
    if not config.DB_URL or not items:
        return []
    try:
        with db_cursor(commit=True) as cur:
            ids = []
            for it in items:
                cur.execute("""INSERT INTO deadlines (student_id, document_id, course, title, due_date, source_snippet, status)
                               VALUES (%s, %s, %s, %s, %s, %s, 'detected') RETURNING id""",
                            (student_id, document_id, course, it["title"], it["due_date"], it.get("source_snippet", "")))
                ids.append(cur.fetchone()["id"])
            return ids
    except Exception as e:
        log_error("services.deadlines.insert_deadlines", e)
        return []


_DEADLINE_STATUSES = {"detected", "confirmed", "corrected", "superseded"}

PERSONAL_ITEM_CATEGORIES = [
    "Doctor's Appointment", "Personal", "Work", "Meeting", "Travel", "Exam", "Other",
]

PERSONAL_ITEM_COLORS = [
    {"name": "Orange", "hex": "#FF8200"},
    {"name": "Navy", "hex": "#002855"},
    {"name": "Green", "hex": "#22C55E"},
    {"name": "Purple", "hex": "#A855F7"},
    {"name": "Blue", "hex": "#0EA5E9"},
    {"name": "Red", "hex": "#EF4444"},
    {"name": "Pink", "hex": "#EC4899"},
    {"name": "Teal", "hex": "#14B8A6"},
    {"name": "Yellow", "hex": "#EAB308"},
    {"name": "Gray", "hex": "#94A3B8"},
]
_PERSONAL_ITEM_COLOR_HEXES = {c["hex"] for c in PERSONAL_ITEM_COLORS}

PERSONAL_ITEM_MAX_OCCURRENCES = 366


def _expand_recurrence(start, frequency, until):
    if frequency not in ("daily", "weekly", "monthly") or not until:
        return [start]
    dates = [start]
    # Named `running_date`, not `cur` — nothing here is a database cursor,
    # and this file uses `cur` for that everywhere else; reusing the name
    # for an unrelated date value was a needless trap for a quick reader.
    running_date = start
    while len(dates) < PERSONAL_ITEM_MAX_OCCURRENCES:
        if frequency == "daily":
            running_date = running_date + timedelta(days=1)
        elif frequency == "weekly":
            running_date = running_date + timedelta(days=7)
        else:
            month = running_date.month + 1
            year = running_date.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            last_day = _pycalendar.monthrange(year, month)[1]
            running_date = running_date.replace(year=year, month=month, day=min(running_date.day, last_day))
        if running_date > until:
            break
        dates.append(running_date)
    return dates


def add_personal_item(student_id, title, due_date, category, color=None, frequency=None, recurrence_end=None):
    if not config.DB_URL:
        return []
    if color not in _PERSONAL_ITEM_COLOR_HEXES:
        color = None
    try:
        start = datetime.strptime(due_date, "%Y-%m-%d").date()
        until = datetime.strptime(recurrence_end, "%Y-%m-%d").date() if recurrence_end else None
        dates = _expand_recurrence(start, frequency, until)
        series_id = secrets.token_hex(8) if frequency and len(dates) > 1 else None
        with db_cursor(commit=True) as cur:
            ids = []
            for d in dates:
                cur.execute("""INSERT INTO deadlines
                               (student_id, document_id, course, title, due_date, source_snippet,
                                status, is_personal, series_id, color, confirmed_at)
                               VALUES (%s, NULL, %s, %s, %s, '', 'confirmed', TRUE, %s, %s, NOW())
                               RETURNING id""",
                            (student_id, category, title, d.isoformat(), series_id, color))
                ids.append(cur.fetchone()["id"])
            return ids
    except Exception as e:
        log_error("services.deadlines.add_personal_item", e)
        return []


def update_personal_item(deadline_id, student_id, title=None, due_date=None, category=None,
                         color=None, apply_to_series=False):
    if not config.DB_URL:
        return None
    if color is not None and color not in _PERSONAL_ITEM_COLOR_HEXES:
        color = None
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("SELECT series_id FROM deadlines WHERE id=%s AND student_id=%s AND is_personal=TRUE",
                        (deadline_id, student_id))
            row = cur.fetchone()
            if not row:
                return None
            series_id = row["series_id"]

            if apply_to_series and series_id:
                cur.execute("""UPDATE deadlines SET
                               title = COALESCE(%s, title),
                               course = COALESCE(%s, course),
                               color = COALESCE(%s, color)
                               WHERE series_id=%s AND student_id=%s AND is_personal=TRUE""",
                            (title, category, color, series_id, student_id))
            else:
                cur.execute("""UPDATE deadlines SET
                               title = COALESCE(%s, title),
                               course = COALESCE(%s, course),
                               color = COALESCE(%s, color),
                               due_date = COALESCE(%s, due_date)
                               WHERE id=%s AND student_id=%s AND is_personal=TRUE""",
                            (title, category, color, due_date, deadline_id, student_id))

            cur.execute("SELECT id, course, title, due_date, color FROM deadlines WHERE id=%s AND student_id=%s",
                        (deadline_id, student_id))
            updated = cur.fetchone()
            if not updated:
                return None
            updated = dict(updated)
            if updated.get("due_date"):
                updated["due_date"] = updated["due_date"].isoformat()
            return updated
    except Exception as e:
        log_error("services.deadlines.update_personal_item", e)
        return None


def delete_personal_item(deadline_id, student_id, delete_series=False):
    if not config.DB_URL:
        return False
    try:
        with db_cursor(commit=True) as cur:
            if delete_series:
                cur.execute("SELECT series_id FROM deadlines WHERE id=%s AND student_id=%s AND is_personal=TRUE",
                            (deadline_id, student_id))
                row = cur.fetchone()
                if row and row["series_id"]:
                    cur.execute("DELETE FROM deadlines WHERE series_id=%s AND student_id=%s AND is_personal=TRUE",
                                (row["series_id"], student_id))
                else:
                    cur.execute("DELETE FROM deadlines WHERE id=%s AND student_id=%s AND is_personal=TRUE",
                                (deadline_id, student_id))
            else:
                cur.execute("DELETE FROM deadlines WHERE id=%s AND student_id=%s AND is_personal=TRUE",
                            (deadline_id, student_id))
            return cur.rowcount > 0
    except Exception as e:
        log_error("services.deadlines.delete_personal_item", e)
        return False


def set_deadline_status(deadline_id, student_id, status, title=None, due_date=None):
    if not config.DB_URL or status not in _DEADLINE_STATUSES:
        return None
    try:
        with db_cursor(commit=True) as cur:
            if title is not None or due_date is not None:
                cur.execute("""UPDATE deadlines SET
                               title = COALESCE(%s, title),
                               due_date = COALESCE(%s, due_date),
                               status = %s, confirmed_at = NOW()
                               WHERE id=%s AND student_id=%s
                               RETURNING id, title, due_date, status""",
                            (title, due_date, status, deadline_id, student_id))
            else:
                cur.execute("""UPDATE deadlines SET status = %s,
                               confirmed_at = CASE WHEN %s IN ('confirmed','corrected') THEN NOW() ELSE confirmed_at END
                               WHERE id=%s AND student_id=%s
                               RETURNING id, title, due_date, status""",
                            (status, status, deadline_id, student_id))
            updated = cur.fetchone()
            return dict(updated) if updated else None
    except Exception as e:
        log_error("services.deadlines.set_deadline_status", e)
        return None


def set_deadline_completed(deadline_id, student_id, completed):
    if not config.DB_URL:
        return None
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("""UPDATE deadlines SET completed = %s,
                           completed_at = CASE WHEN %s THEN NOW() ELSE NULL END
                           WHERE id=%s AND student_id=%s
                           RETURNING id, title, due_date, completed""",
                        (bool(completed), bool(completed), deadline_id, student_id))
            updated = cur.fetchone()
            return dict(updated) if updated else None
    except Exception as e:
        log_error("services.deadlines.set_deadline_completed", e)
        return None


def get_deadline_confirmation_stats():
    if not config.DB_URL:
        return None
    try:
        with db_cursor() as cur:
            cur.execute("SELECT status, COUNT(*) as n FROM deadlines GROUP BY status")
            counts = {r["status"]: r["n"] for r in cur.fetchall()}
        confirmed, corrected = counts.get("confirmed", 0), counts.get("corrected", 0)
        reviewed = confirmed + corrected
        return {
            "detected": counts.get("detected", 0),
            "confirmed": confirmed,
            "corrected": corrected,
            "superseded": counts.get("superseded", 0),
            "reviewed_count": reviewed,
            "correction_rate_pct": round(corrected / reviewed * 100, 1) if reviewed else None,
        }
    except Exception as e:
        log_error("services.deadlines.get_deadline_confirmation_stats", e)
        return None


def get_upcoming_deadlines(sid, days_ahead=14, confirmed_only=False):
    if not config.DB_URL:
        return []
    try:
        query = """SELECT id, course, title, due_date, status, completed FROM deadlines
                   WHERE student_id=%s AND due_date >= (NOW() AT TIME ZONE %s)::date
                   AND due_date <= (NOW() AT TIME ZONE %s)::date + %s::int"""
        if confirmed_only:
            query += " AND status IN ('confirmed','corrected')"
        query += " ORDER BY due_date ASC"
        with db_cursor() as cur:
            cur.execute(query, (sid, config.APP_TIMEZONE, config.APP_TIMEZONE, days_ahead))
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["due_date"] = r["due_date"].isoformat()
        return rows
    except Exception as e:
        log_error("services.deadlines.get_upcoming_deadlines", e); return []


def get_all_deadlines(sid):
    if not config.DB_URL:
        return []
    try:
        with db_cursor() as cur:
            cur.execute("""SELECT dl.id, dl.course, dl.title, dl.due_date, dl.status,
                                  dl.source_snippet, dl.confirmed_at,
                                  dl.document_id, d.orig_name as document_name,
                                  dl.is_personal, dl.series_id, dl.color, dl.completed
                           FROM deadlines dl LEFT JOIN documents d ON d.id = dl.document_id
                           WHERE dl.student_id=%s ORDER BY dl.due_date ASC""", (sid,))
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["due_date"] = r["due_date"].isoformat() if r["due_date"] else None
            r["confirmed_at"] = r["confirmed_at"].isoformat() if r["confirmed_at"] else None
        return rows
    except Exception as e:
        log_error("services.deadlines.get_all_deadlines", e); return []


def build_study_plan(sid, weeks_ahead=4):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from .practice import get_due_questions

    # Local timezone, not UTC — see the note on extract_deadlines() above;
    # "this week" needs to mean the same thing here as it does everywhere
    # else deadlines are grouped by week.
    today = datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()
    rows = [r for r in get_all_deadlines(sid) if r.get("due_date")]
    due_questions = get_due_questions(sid, limit=200)

    weeks = []
    for w in range(weeks_ahead):
        week_start = today + timedelta(days=7 * w)
        week_end = week_start + timedelta(days=6)
        week_deadlines = [
            r for r in rows
            if week_start.isoformat() <= r["due_date"] <= week_end.isoformat()
        ]
        review_count = len(due_questions) if w == 0 else 0
        weeks.append({
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "deadlines": sorted(week_deadlines, key=lambda r: r["due_date"]),
            "questions_due_for_review": review_count,
        })
    return weeks


def detect_deadline_conflicts(sid, window_days=5, min_items=3):
    rows = get_all_deadlines(sid)
    dated = [r for r in rows if r.get("due_date")]
    dated.sort(key=lambda r: r["due_date"])

    clusters = []
    current = []
    prev_date = None
    for r in dated:
        d = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
        if prev_date is not None and (d - prev_date).days > window_days:
            if len(current) >= min_items:
                clusters.append(current)
            current = []
        current.append(r)
        prev_date = d
    if len(current) >= min_items:
        clusters.append(current)
    return clusters


def build_deadlines_context(sid):
    if not config.DB_URL:
        return "\n\nNo deadline data available (no database configured)."
    rows = get_all_deadlines(sid)
    if not rows:
        return ("\n\nNo deadlines have been extracted yet. This can mean the student's "
                "documents don't contain a schedule of specific dates, or nothing has "
                "been uploaded yet — don't invent dates that aren't in this list.")
    unconfirmed = [r for r in rows if r.get("status", "detected") == "detected"]
    lines = [f"\n\n{'='*60}\nEXTRACTED DEADLINES — every date-specific item found across "
             f"ALL of the student's uploaded documents ({len(rows)} total). This list is "
             "COMPLETE and NOT truncated, unlike the raw document text below — always use "
             "this list (not the raw text) when asked for a calendar, schedule, or 'what's "
             f"due' summary.\n{'='*60}"]
    if unconfirmed:
        lines.append(
            f"NOTE: {len(unconfirmed)} of these are AI-extracted and not yet confirmed by the "
            "student ('detected' status below) — present them as possible deadlines to verify "
            "against the syllabus, not as certain. Encourage the student to confirm or correct "
            "them on their calendar page. Never state a 'detected' deadline with the same "
            "confidence as a 'confirmed' or 'corrected' one."
        )
    for r in rows:
        due = r["due_date"] or "date unknown"
        status = r.get("status", "detected")
        tag = "" if status in ("confirmed", "corrected") else " [unconfirmed]"
        lines.append(f"- [{r['course']}] {r['title']} — due {due}{tag}")
    lines.append(f"{'='*60}")

    conflicts = detect_deadline_conflicts(sid)
    if conflicts:
        lines.append(
            "\nHEADS UP — busy stretches: the ranges below have several deadlines landing "
            "close together. Proactively mention this ONCE if the student asks about their "
            "calendar, schedule, or workload — don't force it into every unrelated answer:"
        )
        for cluster in conflicts:
            start, end = cluster[0]["due_date"], cluster[-1]["due_date"]
            items = "; ".join(f"{r['course']}: {r['title']} ({r['due_date']})" for r in cluster)
            lines.append(f"- {start} to {end} ({len(cluster)} items): {items}")
    lines.append("")
    return "\n".join(lines)
