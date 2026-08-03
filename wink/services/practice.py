"""
Generates new practice questions from a student's own uploaded material —
optionally styled after a real past assessment they've also uploaded
(a past exam, quiz, or study guide), so the practice questions match the
format and difficulty their actual instructor uses, not a generic quiz
format that might not resemble what they'll really be tested on.

The assessment (if provided) is used ONLY as a style/format reference —
never as the source of facts for the generated questions, and the model is
explicitly told not to just reword or repeat questions from it. The
factual content always comes from the student's course material (syllabus,
notes, slides — whatever they've uploaded and tagged as 'material', which
is every upload by default).
"""
import json
from datetime import timedelta

from .. import config
from ..errors import log_error
from ..extensions import anthropic_client, get_db
from ..timeutil import utcnow_naive


def generate_practice_questions(material_text, assessment_text=None, count=8):
    """Returns a list of {"question": str, "answer": str, "explanation": str}
    dicts, or [] on any failure (no API key, no material, model error,
    unparsable response). `assessment_text`, if given, is used purely as a
    style/format example — see module docstring."""
    if not anthropic_client or not (material_text or "").strip():
        return []

    count = max(1, min(int(count), 15))

    if assessment_text and assessment_text.strip():
        style_instruction = (
            "The user will also give you a sample of a REAL past assessment from this course "
            "(an old exam, quiz, or study guide). Use it ONLY to match its style: question "
            "format (multiple choice, short answer, etc.), typical difficulty, and phrasing "
            "conventions. Do NOT reuse, reword, or lightly disguise any specific question from "
            "the sample assessment — every question you write must be new and about different "
            "specific facts than whatever appears in that sample, even if the general topic "
            "overlaps. The sample is a style guide, not a content source."
        )
    else:
        style_instruction = (
            "No sample assessment was provided, so use a straightforward mix of short-answer "
            "and multiple-choice questions at a level appropriate for the material."
        )

    system = (
        f"You are generating {count} practice questions for a college student studying from "
        f"their own uploaded course material. {style_instruction} "
        "Every question must be answerable from the course material given to you — never invent "
        "facts or ask about anything not actually in it. Respond with ONLY a JSON array (no "
        "prose, no markdown fences) of objects shaped like "
        '{"question": "...", "answer": "...", "explanation": "..."}. '
        "\"answer\" is the correct answer stated plainly; \"explanation\" is 1-2 sentences on "
        "why, referencing the material. If the material genuinely doesn't contain enough to "
        "write good questions, return fewer than requested rather than padding with vague ones."
    )

    user_content = f"COURSE MATERIAL:\n{material_text[:config.PRACTICE_MATERIAL_MAX_CHARS]}"
    if assessment_text and assessment_text.strip():
        user_content += f"\n\nSAMPLE PAST ASSESSMENT (style reference only):\n{assessment_text[:config.PRACTICE_ASSESSMENT_MAX_CHARS]}"

    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[-1] if "\n" in raw else raw.strip("`")
        items = json.loads(raw)
        out = []
        for it in items if isinstance(items, list) else []:
            q = str(it.get("question", "")).strip()
            a = str(it.get("answer", "")).strip()
            if q and a:
                out.append({
                    "question": q[:1000], "answer": a[:1000],
                    "explanation": str(it.get("explanation", "")).strip()[:1000],
                })
        return out[:count]
    except Exception as e:
        log_error("services.practice.generate_practice_questions", e)
        return []


# ── Spaced repetition ──────────────────────────────────────────
# Simple, well-established scheme (a lightweight variant of the classic
# Leitner/SM-2 family, not the full SM-2 algorithm — deliberately kept
# simple over "as accurate as possible", since the actual evidence for
# spaced repetition's benefit holds across a wide range of interval
# schemes; the core win is "spaced at all" vs. "crammed once"):
#   - Start at a 1-day interval.
#   - Each correct answer roughly triples the interval (1 -> 3 -> 9 -> 27...
#     days), capped at 60 days so a well-known question doesn't vanish
#     from review for months.
#   - Any incorrect answer resets straight back to a 1-day interval,
#     regardless of prior streak — a lapse means the material needs
#     restudying soon, not "slightly sooner than before."
_MAX_INTERVAL_DAYS = 60


def schedule_next_review(current_interval_days, correct):
    """Returns (new_interval_days, new_next_review_date) given whether the
    most recent attempt was correct. Pure function — no DB access — so
    it's directly testable without a database."""
    if correct:
        new_interval = min(current_interval_days * 3, _MAX_INTERVAL_DAYS)
    else:
        new_interval = 1
    next_review = (utcnow_naive() + timedelta(days=new_interval)).date()
    return new_interval, next_review


def store_practice_questions(student_id, course, questions):
    """Persists generated questions so they can be reviewed later (see
    get_due_questions()/record_attempt() below), instead of only ever being
    shown once and discarded. Returns the same questions with a real "id"
    added to each, so the caller (the /generate-practice route) can hand
    the student's browser something it can later reference in a
    record_attempt() call."""
    if not config.DB_URL or not questions:
        return questions
    try:
        conn = get_db(); cur = conn.cursor()
        stored = []
        for q in questions:
            cur.execute("""INSERT INTO practice_questions
                           (student_id, course, question, answer, explanation)
                           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                        (student_id, course, q["question"], q["answer"], q.get("explanation", "")))
            new_id = cur.fetchone()["id"]
            stored.append({**q, "id": new_id})
        conn.commit(); cur.close()
        return stored
    except Exception as e:
        log_error("services.practice.store_practice_questions", e)
        return questions


def record_attempt(student_id, question_id, correct):
    """Records whether a student got a specific practice question right or
    wrong just now, and reschedules it accordingly. Returns the updated
    row, or None if the question doesn't exist or doesn't belong to this
    student (ownership is enforced in the UPDATE's WHERE clause, not
    checked separately beforehand, so there's no window between check and
    update)."""
    if not config.DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT interval_days FROM practice_questions WHERE id=%s AND student_id=%s",
                    (question_id, student_id))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None
        new_interval, next_review = schedule_next_review(row["interval_days"], correct)
        cur.execute("""UPDATE practice_questions
                       SET interval_days=%s, next_review_date=%s, last_attempted_at=NOW(),
                           correct_streak = CASE WHEN %s THEN correct_streak + 1 ELSE 0 END
                       WHERE id=%s AND student_id=%s
                       RETURNING id, interval_days, next_review_date, correct_streak""",
                    (new_interval, next_review, correct, question_id, student_id))
        updated = cur.fetchone()
        conn.commit(); cur.close()
        if updated:
            updated = dict(updated)
            updated["next_review_date"] = updated["next_review_date"].isoformat()
        return updated
    except Exception as e:
        log_error("services.practice.record_attempt", e)
        return None


def get_due_questions(student_id, course=None, limit=20):
    """Questions whose next_review_date has arrived (today or earlier) —
    what a "review" session should actually show the student, as opposed
    to every practice question ever generated for them."""
    if not config.DB_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        if course:
            cur.execute("""SELECT id, course, question, answer, explanation, correct_streak, next_review_date
                           FROM practice_questions
                           WHERE student_id=%s AND lower(course)=lower(%s) AND next_review_date <= CURRENT_DATE
                           ORDER BY next_review_date ASC LIMIT %s""", (student_id, course, limit))
        else:
            cur.execute("""SELECT id, course, question, answer, explanation, correct_streak, next_review_date
                           FROM practice_questions
                           WHERE student_id=%s AND next_review_date <= CURRENT_DATE
                           ORDER BY next_review_date ASC LIMIT %s""", (student_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["next_review_date"] = r["next_review_date"].isoformat()
        return rows
    except Exception as e:
        log_error("services.practice.get_due_questions", e)
        return []
