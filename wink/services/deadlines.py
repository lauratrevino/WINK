"""Deadline extraction (one small model call per upload) and the queries
that back the dashboard, calendar page, and chat context."""
import json
from datetime import datetime

from .. import config
from ..errors import log_error
from ..extensions import get_db, anthropic_client
from ..timeutil import utcnow_naive


def extract_deadlines(content, today=None):
    """Ask Claude to pull structured (title, due_date, source_snippet) triples
    out of a document's text — e.g. a syllabus's assignment schedule. Returns
    a list of {"title": str, "due_date": "YYYY-MM-DD", "source_snippet": str}
    dicts, or [] on any failure (no document content, model error, unparsable
    response, etc). This is a small, cheap, one-time Haiku call made once per
    upload, not per question.

    source_snippet is the actual sentence(s) the date/title came from, kept
    short (<=200 chars) — every extracted deadline is a model guess until a
    student (or a reviewer, on the research page) checks it against real
    document text, and a snippet is what makes that checkable instead of
    forcing someone to re-read the whole syllabus. Every extracted deadline
    is stored with status='detected' (see insert_deadlines() below) and
    should not be treated as confirmed until a human says so."""
    if not anthropic_client or not content or not content.strip():
        return []
    today = today or utcnow_naive().strftime("%Y-%m-%d")
    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            max_tokens=1200,
            system=(
                "Extract assignment, exam, and other academic deadlines from the "
                "document text the user provides. Respond with ONLY a JSON array "
                "(no prose, no markdown fences) of objects shaped like "
                '{"title": "...", "due_date": "YYYY-MM-DD", "source_snippet": "..."}. '
                "source_snippet must be the actual sentence (or short span, under 200 "
                "characters) from the document that the title/due_date were read from — "
                "never paraphrase or invent it; copy it from the text given to you. "
                f"Today's date is {today} — resolve relative or partial dates "
                "(e.g. \"March 3\" with no year, or \"next Friday\") against it. "
                "Skip anything without a specific date you can resolve. "
                "If there are no clear deadlines, respond with []."
            ),
            messages=[{"role": "user", "content": content[:config.DEADLINE_EXTRACTION_MAX_CHARS]}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[-1] if "\n" in raw else raw.strip("`")
        items = json.loads(raw)
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
        return out[:30]
    except Exception as e:
        log_error("services.deadlines.extract_deadlines", e)
        return []


def insert_deadlines(student_id, document_id, course, items):
    """Stores extracted deadlines with status='detected' — the single
    insertion point every upload path should call instead of writing its own
    INSERT, so the confirmation-state contract (nothing starts 'confirmed')
    can't be bypassed by a route that predates it. Returns the new row ids in
    the same order as `items`, or [] on failure."""
    if not config.DB_URL or not items:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        ids = []
        for it in items:
            cur.execute("""INSERT INTO deadlines (student_id, document_id, course, title, due_date, source_snippet, status)
                           VALUES (%s, %s, %s, %s, %s, %s, 'detected') RETURNING id""",
                        (student_id, document_id, course, it["title"], it["due_date"], it.get("source_snippet", "")))
            ids.append(cur.fetchone()["id"])
        conn.commit(); cur.close()
        return ids
    except Exception as e:
        log_error("services.deadlines.insert_deadlines", e)
        return []


_DEADLINE_STATUSES = {"detected", "confirmed", "corrected", "superseded"}


def set_deadline_status(deadline_id, student_id, status, title=None, due_date=None):
    """Student-driven status change — 'confirmed' (verified as-is against
    their syllabus) or 'corrected' (student edited title/due_date, which this
    also applies in the same call) or 'superseded' (a re-upload replaced it).
    Ownership is enforced in the WHERE clause, same pattern as
    practice.py's record_attempt(). Returns the updated row, or None if the
    id doesn't exist / doesn't belong to this student / status is invalid."""
    if not config.DB_URL or status not in _DEADLINE_STATUSES:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
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
        conn.commit(); cur.close()
        return dict(updated) if updated else None
    except Exception as e:
        log_error("services.deadlines.set_deadline_status", e)
        return None


def get_deadline_confirmation_stats():
    """Counts of extracted deadlines by confirmation status, across every
    student — the rough, human-in-the-loop precision proxy the WINK review
    asked for in place of no accuracy signal at all: every 'corrected'
    deadline is a case where the AI's first guess was wrong in some way a
    student had to notice and fix, so corrected / (confirmed + corrected) is
    an honest (if approximate — it only catches errors a student actually
    caught) error rate for the extraction model. Returns None for that rate
    if nobody has confirmed or corrected anything yet, rather than a
    misleading 0%."""
    if not config.DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) as n FROM deadlines GROUP BY status")
        counts = {r["status"]: r["n"] for r in cur.fetchall()}
        cur.close()
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
    """`confirmed_only=True` restricts to status IN ('confirmed','corrected')
    — use this for anything that reads as a strong commitment (a reminder
    email, a push notification) rather than an in-app list the student is
    expected to still review. Defaults to False (all statuses) to keep
    existing dashboard behavior unchanged."""
    if not config.DB_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        query = """SELECT id, course, title, due_date, status FROM deadlines
                   WHERE student_id=%s AND due_date >= CURRENT_DATE
                   AND due_date <= CURRENT_DATE + %s::int"""
        if confirmed_only:
            query += " AND status IN ('confirmed','corrected')"
        query += " ORDER BY due_date ASC"
        cur.execute(query, (sid, days_ahead))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["due_date"] = r["due_date"].isoformat()
        return rows
    except Exception as e:
        log_error("services.deadlines.get_upcoming_deadlines", e); return []


def get_all_deadlines(sid):
    """Every deadline row for a student, no date-range cap — the single
    source of truth used by both the chat context (as text) and the visual
    calendar page (as JSON)."""
    if not config.DB_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT dl.id, dl.course, dl.title, dl.due_date, dl.status,
                              dl.source_snippet, dl.confirmed_at,
                              dl.document_id, d.orig_name as document_name
                       FROM deadlines dl LEFT JOIN documents d ON d.id = dl.document_id
                       WHERE dl.student_id=%s ORDER BY dl.due_date ASC""", (sid,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["due_date"] = r["due_date"].isoformat() if r["due_date"] else None
            r["confirmed_at"] = r["confirmed_at"].isoformat() if r["confirmed_at"] else None
        return rows
    except Exception as e:
        log_error("services.deadlines.get_all_deadlines", e); return []


def build_study_plan(sid, weeks_ahead=4):
    """A week-by-week plan combining what's already known separately:
    upcoming deadlines (this module) and practice questions due for
    review (services/practice.py). Doesn't compute anything new about
    either — it's a merge, presented as one plan instead of two separate
    dashboards, for the next `weeks_ahead` weeks starting today."""
    from datetime import timedelta
    from ..timeutil import utcnow_naive
    from .practice import get_due_questions

    today = utcnow_naive().date()
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
        # Practice review load "for this week" is only meaningfully known for
        # week 0 (today) — get_due_questions() only returns what's due as of
        # right now, not a future projection of what will accumulate by
        # then, so later weeks show 0 rather than a misleading guess.
        review_count = len(due_questions) if w == 0 else 0
        weeks.append({
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "deadlines": sorted(week_deadlines, key=lambda r: r["due_date"]),
            "questions_due_for_review": review_count,
        })
    return weeks


def detect_deadline_conflicts(sid, window_days=5, min_items=3):
    """Groups a student's deadlines into date-proximity clusters —
    consecutive deadlines no more than window_days apart land in the same
    cluster — and returns any cluster with at least min_items in it. This
    is a rough "you have a lot landing around the same time" signal, not a
    precise scheduling conflict detector: it doesn't know how long any one
    assignment actually takes, or a student's other commitments, just how
    many extracted deadlines happen to sit close together on the
    calendar."""
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
    """Every deadline extracted from every one of the student's uploaded
    documents, across every course, with no date-range cap and no
    truncation. This is deliberately separate from build_doc_context()'s
    truncated raw document text — a "build me a master calendar" question
    shouldn't depend on how much raw document text fit under the
    per-message cost cap, since the structured deadline data is already
    small and already complete."""
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
