import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..errors import log_error
from ..extensions import anthropic_client, get_db
from .analytics import log_token_usage
from .json_utils import parse_json_array, strip_json_fence


def generate_practice_questions(material_text, assessment_text=None, count=8, qtype="review", student_id=None):
    if not anthropic_client or not (material_text or "").strip():
        return []

    count = max(1, min(int(count), 15))
    is_mc = qtype in ("quiz", "assessment_quiz")

    if assessment_text and assessment_text.strip():
        style_instruction = (
            "The user will also give you a sample of a REAL past assessment from this course "
            "(an old exam, quiz, or study guide). Use it ONLY to match its style: question "
            "format, typical difficulty, and phrasing conventions. Do NOT reuse, reword, or "
            "lightly disguise any specific question from the sample assessment — every question "
            "you write must be new and about different specific facts than whatever appears in "
            "that sample, even if the general topic overlaps. The sample is a style guide, not a "
            "content source."
        )
    else:
        style_instruction = (
            "No sample assessment was provided, so use a straightforward, appropriate difficulty "
            "for the material."
        )

    if qtype == "flashcard":
        format_instruction = (
            "Write short FLASHCARDS: a brief prompt (a term, a concept, a short question) on one "
            "side and a concise answer on the other — quick recall, not deep explanation. Respond "
            "with ONLY a JSON array (no prose, no markdown fences) of objects shaped like "
            '{"question": "...", "answer": "..."}. Keep both sides short — a phrase or one sentence.'
        )
    elif is_mc:
        format_instruction = (
            "Write MULTIPLE CHOICE questions, each with exactly 4 options where exactly one is "
            "correct and the other 3 are plausible but clearly wrong to someone who knows the "
            "material. Respond with ONLY a JSON array (no prose, no markdown fences) of objects "
            'shaped like {"question": "...", "options": ["...", "...", "...", "..."], '
            '"correct_index": 0, "explanation": "..."}. "correct_index" is the 0-based index into '
            '"options" of the correct answer. "explanation" is 1-2 sentences on why that answer is '
            "correct — this is shown to students who pick a wrong option, so make it genuinely "
            "explain the concept, not just restate the answer."
        )
    else:  
        format_instruction = (
            "Write open-ended short-answer and multiple-choice questions mixed as appropriate. "
            "Respond with ONLY a JSON array (no prose, no markdown fences) of objects shaped like "
            '{"question": "...", "answer": "...", "explanation": "..."}. "answer" is the correct '
            'answer stated plainly; "explanation" is 1-2 sentences on why, referencing the material.'
        )

    system = (
        f"You are generating {count} practice questions for a college student studying from "
        f"their own uploaded course material. {style_instruction} {format_instruction} "
        "Every question must be answerable from the course material given to you — never invent "
        "facts or ask about anything not actually in it. If the material genuinely doesn't "
        "contain enough to write good questions, return fewer than requested rather than padding "
        "with vague ones."
    )

    user_content = f"COURSE MATERIAL:\n{material_text[:config.PRACTICE_MATERIAL_MAX_CHARS]}"
    if assessment_text and assessment_text.strip():
        user_content += f"\n\nSAMPLE PAST ASSESSMENT (style reference only):\n{assessment_text[:config.PRACTICE_ASSESSMENT_MAX_CHARS]}"

    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            max_tokens=4096 if is_mc else 2048,  
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        if student_id is not None:
            log_token_usage(student_id, "practice_generation", config.CHAT_MODEL, resp.usage)
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        raw = strip_json_fence(raw)
        items = parse_json_array(raw)
        out = []
        for it in items if isinstance(items, list) else []:
            q = str(it.get("question", "")).strip()
            if not q:
                continue
            if qtype == "flashcard":
                a = str(it.get("answer", "")).strip()
                if a:
                    out.append({"question": q[:500], "answer": a[:500], "explanation": ""})
            elif is_mc:
                options = it.get("options")
                idx = it.get("correct_index")
                if (isinstance(options, list) and len(options) == 4
                        and all(str(o).strip() for o in options)
                        and isinstance(idx, int) and 0 <= idx < 4):
                    out.append({
                        "question": q[:1000],
                        "options": [str(o).strip()[:300] for o in options],
                        "correct_index": idx,
                        "explanation": str(it.get("explanation", "")).strip()[:1000],
                        "answer": str(options[idx]).strip()[:300],
                    })
            else:
                a = str(it.get("answer", "")).strip()
                if a:
                    out.append({
                        "question": q[:1000], "answer": a[:1000],
                        "explanation": str(it.get("explanation", "")).strip()[:1000],
                    })
        return out[:count]
    except Exception as e:
        log_error("services.practice.generate_practice_questions", e)
        return []


def generate_practice_summary(material_text, course, student_id=None):
    if not anthropic_client or not (material_text or "").strip():
        return ""
    system = (
        f"You are writing a clear, well-organized study summary of a college student's course "
        f"material for {course or 'their course'}. Cover the key concepts, definitions, and "
        "relationships between ideas — organized under a few short headings, using markdown "
        "(## headings, bullet points, **bold** for key terms). Do not invent facts not present "
        "in the material. Aim for genuinely useful exam-prep density, not a padded restatement."
    )
    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": f"COURSE MATERIAL:\n{material_text[:config.PRACTICE_MATERIAL_MAX_CHARS]}"}],
        )
        if student_id is not None:
            log_token_usage(student_id, "practice_summary", config.CHAT_MODEL, resp.usage)
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    except Exception as e:
        log_error("services.practice.generate_practice_summary", e)
        return ""


_MAX_INTERVAL_DAYS = 60


def schedule_next_review(current_interval_days, correct):
    if correct:
        new_interval = min(current_interval_days * 3, _MAX_INTERVAL_DAYS)
    else:
        new_interval = 1
    local_today = datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()
    next_review = local_today + timedelta(days=new_interval)
    return new_interval, next_review


def store_practice_questions(student_id, course, questions, qtype="review"):
    if not config.DB_URL or not questions:
        return questions
    try:
        conn = get_db(); cur = conn.cursor()
        stored = []
        for q in questions:
            options_json = json.dumps(q["options"]) if "options" in q else None
            cur.execute("""INSERT INTO practice_questions
                           (student_id, course, question, answer, explanation, qtype, options, correct_index)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (student_id, course, q["question"], q.get("answer", ""), q.get("explanation", ""),
                         qtype, options_json, q.get("correct_index")))
            new_id = cur.fetchone()["id"]
            stored.append({**q, "id": new_id, "qtype": qtype})
        conn.commit(); cur.close()
        return stored
    except Exception as e:
        log_error("services.practice.store_practice_questions", e)
        return questions


def record_attempt(student_id, question_id, correct):
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
    if not config.DB_URL:
        return []
    try:
        conn = get_db(); cur = conn.cursor()
        cols = "id, course, question, answer, explanation, correct_streak, next_review_date, qtype, options, correct_index"
        if course:
            cur.execute(f"""SELECT {cols}
                           FROM practice_questions
                           WHERE student_id=%s AND lower(course)=lower(%s) AND next_review_date <= (NOW() AT TIME ZONE %s)::date
                           ORDER BY next_review_date ASC LIMIT %s""", (student_id, course, config.APP_TIMEZONE, limit))
        else:
            cur.execute(f"""SELECT {cols}
                           FROM practice_questions
                           WHERE student_id=%s AND next_review_date <= (NOW() AT TIME ZONE %s)::date
                           ORDER BY next_review_date ASC LIMIT %s""", (student_id, config.APP_TIMEZONE, limit))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["next_review_date"] = r["next_review_date"].isoformat()
            r["options"] = json.loads(r["options"]) if r.get("options") else None
        return rows
    except Exception as e:
        log_error("services.practice.get_due_questions", e)
        return []


def grade_quiz_answer(student_id, question_id, selected_index):
    if not config.DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT interval_days, options, correct_index, explanation
                       FROM practice_questions WHERE id=%s AND student_id=%s""",
                    (question_id, student_id))
        row = cur.fetchone()
        if not row or row["correct_index"] is None or not row["options"]:
            cur.close()
            return None
        correct_index = row["correct_index"]
        options = json.loads(row["options"])
        correct = (selected_index == correct_index)
        new_interval, next_review = schedule_next_review(row["interval_days"], correct)
        cur.execute("""UPDATE practice_questions
                       SET interval_days=%s, next_review_date=%s, last_attempted_at=NOW(),
                           correct_streak = CASE WHEN %s THEN correct_streak + 1 ELSE 0 END
                       WHERE id=%s AND student_id=%s""",
                    (new_interval, next_review, correct, question_id, student_id))
        conn.commit(); cur.close()
        return {
            "correct": correct,
            "selected_index": selected_index,
            "correct_index": correct_index,
            "options": options,
            "explanation": row["explanation"] or "",
        }
    except Exception as e:
        log_error("services.practice.grade_quiz_answer", e)
        return None
