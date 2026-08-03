"""
Extracts a course's grading-weight breakdown (e.g. "Homework 20%, Midterm
30%, Final 30%, Participation 20%") from its uploaded material, once —
same one-time-extraction pattern as services/deadlines.py's
extract_deadlines(): a small, cheap Haiku call made when the student asks
for it, not re-run on every question.

Freshmen are the intended audience for the calculator this backs (see
grades.html): a first-semester student may not know to ask the right chat
question, and has no instinct yet for noticing a subtly wrong
weighted-average answer. Showing the actual extracted categories and
weights as plain, editable numbers gives them something concrete to check
against their own syllabus, and means a wrong extraction is wrong in one
visible, fixable place instead of silently differently wrong in every
chat answer.

NOTE: rebuilt from a description of an earlier version of this file (the
original was written in a different conversation and isn't available to
copy from directly) — behavior matches what was documented at the time,
but treat this as a fresh implementation, not a byte-for-byte restore.
"""
import json

from .. import config
from ..errors import log_error
from ..extensions import anthropic_client, get_db


def extract_grading_weights(content):
    """Returns a list of {"category": str, "weight": float} dicts (weight
    as a percentage, e.g. 20.0 for 20%) extracted from a course's combined
    uploaded material, or [] on any failure (no client configured, no
    content, model error, unparsable response). Call this from the
    /extract-grading-weights route — not per chat question."""
    if not anthropic_client or not content or not content.strip():
        return []
    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            max_tokens=600,
            system=(
                "Extract the grading/weighting breakdown from the course material given to "
                "you (e.g. a syllabus's 'Grading', 'Course Requirements', or 'Evaluation' "
                "section) — the categories that make up the final grade and each one's "
                "percentage weight. Respond with ONLY a JSON array (no prose, no markdown "
                'fences) of objects shaped like {"category": "...", "weight": 20.0}. Weight '
                "is a plain percentage number (20.0 for 20%, not 0.2). Keep categories named "
                "exactly as the syllabus names them; don't invent or split categories that "
                "the document presents as one. If there is no clear grading breakdown in the "
                "material, respond with []."
            ),
            messages=[{"role": "user", "content": content[:config.PRACTICE_MATERIAL_MAX_CHARS]}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[-1] if "\n" in raw else raw.strip("`")
        items = json.loads(raw)
        out = []
        for it in items if isinstance(items, list) else []:
            category = str(it.get("category", "")).strip()[:100]
            try:
                weight = float(it.get("weight"))
            except (TypeError, ValueError):
                continue
            if category and weight > 0:
                out.append({"category": category, "weight": weight})
        return out[:20]
    except Exception as e:
        log_error("services.grades.extract_grading_weights", e)
        return []


def get_grading_weights(student_id, course):
    """This student's stored weights for one course, in display order — []
    if nothing's been extracted or saved yet, which the page treats as
    "show the extract button", not an error."""
    if not config.DB_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT category, weight FROM grading_weights
                       WHERE student_id=%s AND lower(course)=lower(%s)
                       ORDER BY sort_order ASC, id ASC""", (student_id, course))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["weight"] = float(r["weight"])
        return rows
    except Exception as e:
        log_error("services.grades.get_grading_weights", e); return []


def store_grading_weights(student_id, course, weights):
    """Replaces this student's stored weights for one course — delete then
    re-insert, the same full-replace-on-save pattern already used
    elsewhere in this app (e.g. a document replace-on-reupload), since a
    student editing the table is replacing the whole breakdown, not
    patching individual rows. Silently no-ops on failure — a save that
    fails shouldn't crash the page, just leave the previous save in
    place."""
    if not config.DB_URL:
        return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM grading_weights WHERE student_id=%s AND lower(course)=lower(%s)",
                    (student_id, course))
        i = 0
        for w in (weights or []):
            category = str(w.get("category", "")).strip()[:100]
            try:
                weight = float(w.get("weight"))
            except (TypeError, ValueError):
                continue
            if category and weight > 0:
                cur.execute("""INSERT INTO grading_weights (student_id, course, category, weight, sort_order)
                               VALUES (%s, %s, %s, %s, %s)""", (student_id, course, category, weight, i))
                i += 1
        conn.commit(); cur.close()
    except Exception as e:
        log_error("services.grades.store_grading_weights", e)
