"""Deadline extraction (one small model call per upload) and the queries
that back the dashboard, calendar page, and chat context."""
import json
from datetime import datetime

from .. import config
from ..extensions import get_db, anthropic_client
from ..timeutil import utcnow_naive


def extract_deadlines(content, today=None):
    """Ask Claude to pull structured (title, due_date) pairs out of a
    document's text — e.g. a syllabus's assignment schedule. Returns a list
    of {"title": str, "due_date": "YYYY-MM-DD"} dicts, or [] on any failure
    (no document content, model error, unparsable response, etc). This is a
    small, cheap, one-time Haiku call made once per upload, not per
    question."""
    if not anthropic_client or not content or not content.strip():
        return []
    today = today or utcnow_naive().strftime("%Y-%m-%d")
    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            max_tokens=800,
            system=(
                "Extract assignment, exam, and other academic deadlines from the "
                "document text the user provides. Respond with ONLY a JSON array "
                "(no prose, no markdown fences) of objects shaped like "
                '{"title": "...", "due_date": "YYYY-MM-DD"}. '
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
                out.append({"title": title, "due_date": due})
        return out[:30]
    except Exception as e:
        print(f"extract_deadlines error: {e}")
        return []


def get_upcoming_deadlines(sid, days_ahead=14):
    if not config.DB_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT id, course, title, due_date FROM deadlines
                       WHERE student_id=%s AND due_date >= CURRENT_DATE
                       AND due_date <= CURRENT_DATE + %s::int
                       ORDER BY due_date ASC""", (sid, days_ahead))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["due_date"] = r["due_date"].isoformat()
        return rows
    except Exception as e:
        print(f"get_upcoming_deadlines error: {e}"); return []


def get_all_deadlines(sid):
    """Every deadline row for a student, no date-range cap — the single
    source of truth used by both the chat context (as text) and the visual
    calendar page (as JSON)."""
    if not config.DB_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT dl.id, dl.course, dl.title, dl.due_date,
                              dl.document_id, d.orig_name as document_name
                       FROM deadlines dl LEFT JOIN documents d ON d.id = dl.document_id
                       WHERE dl.student_id=%s ORDER BY dl.due_date ASC""", (sid,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["due_date"] = r["due_date"].isoformat() if r["due_date"] else None
        return rows
    except Exception as e:
        print(f"get_all_deadlines error: {e}"); return []


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
    lines = [f"\n\n{'='*60}\nEXTRACTED DEADLINES — every date-specific item found across "
             f"ALL of the student's uploaded documents ({len(rows)} total). This list is "
             "COMPLETE and NOT truncated, unlike the raw document text below — always use "
             "this list (not the raw text) when asked for a calendar, schedule, or 'what's "
             f"due' summary.\n{'='*60}"]
    for r in rows:
        due = r["due_date"] or "date unknown"
        lines.append(f"- [{r['course']}] {r['title']} — due {due}")
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
