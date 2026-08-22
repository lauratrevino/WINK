from .. import config
from ..errors import log_error
from ..extensions import anthropic_client, db_cursor
from .analytics import log_token_usage
from .json_utils import parse_json_array, strip_json_fence


def extract_grading_weights(content, student_id=None):
    if not anthropic_client or not content or not content.strip():
        return []
    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            # Raised from 600: splitting grouped categories into individual
            # rows (see prompt below) can produce noticeably more JSON output
            # than one row per group did, and this app has previously hit a
            # silent-truncation bug from an undersized max_tokens on a very
            # similar extraction call (see extract_deadlines' history).
            max_tokens=2048,
            system=(
                "Extract the grading/weighting breakdown from the course material given to "
                "you (e.g. a syllabus's 'Grading', 'Course Requirements', or 'Evaluation' "
                "section) — the categories that make up the final grade and each one's "
                "percentage weight.\n"
                "IMPORTANT — repeated/grouped categories: when the syllabus presents a "
                "category as a group with a count, such as 'Market Labs (4)', 'Quizzes "
                "(5)', or '3 Homework Assignments', DO NOT output one combined row for the "
                "whole group. Instead, output one separate object per individual item, "
                "using the singular form of the name (e.g., 'Market Lab', not 'Market Labs "
                "(4)'), repeated once per item. Work out each individual item's weight from "
                "how the syllabus phrases the group's weight:\n"
                "- If the stated percentage is the TOTAL for the whole group (e.g., 'Labs "
                "(4) — 16%'), divide it evenly across the items (16% / 4 = 4% each).\n"
                "- If the stated percentage is ALREADY per individual item (e.g., '5 "
                "Quizzes at 3% each'), use that same weight for every item as given (3% "
                "each), not divided further.\n"
                "If it's ambiguous which of these the syllabus means, prefer treating the "
                "stated number as the TOTAL for the group and dividing it evenly, since that "
                "is the more common syllabus convention.\n"
                "For categories that are NOT a repeated group (a single Midterm, a single "
                "Final Exam, etc.), keep them as one row named exactly as the syllabus names "
                "it — only split grouped/repeated categories as described above.\n"
                "Respond with ONLY a JSON array (no prose, no markdown "
                'fences) of objects shaped like {"category": "...", "weight": 20.0}. Weight '
                "is a plain percentage number (20.0 for 20%, not 0.2). If there is no clear "
                "grading breakdown in the material, respond with []."
            ),
            messages=[{"role": "user", "content": content[:config.PRACTICE_MATERIAL_MAX_CHARS]}],
        )
        if student_id is not None:
            log_token_usage(student_id, "grade_extraction", config.CHAT_MODEL, resp.usage)
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        raw = strip_json_fence(raw)
        # parse_json_array salvages a complete-so-far array from a
        # truncated/malformed response instead of returning nothing at all
        # on any single parse error — see extract_deadlines()'s history for
        # why an all-or-nothing json.loads() here is worth avoiding.
        items = parse_json_array(raw)
        out = []
        for it in items if isinstance(items, list) else []:
            category = str(it.get("category", "")).strip()[:100]
            try:
                weight = float(it.get("weight"))
            except (TypeError, ValueError):
                continue
            if category and weight > 0:
                out.append({"category": category, "weight": weight})
        # Splitting grouped categories (e.g., "Market Labs (4)" -> 4 separate
        # rows) can legitimately produce more individual rows than the old
        # cap of 20 for a syllabus with several such groups, so this is
        # raised to give room for that without meaningfully changing the
        # worst-case response size.
        return out[:60]
    except Exception as e:
        log_error("services.grades.extract_grading_weights", e)
        return []


def get_grading_weights(student_id, course):
    if not config.DB_URL:
        return []
    try:
        with db_cursor() as cur:
            cur.execute("""SELECT category, weight FROM grading_weights
                           WHERE student_id=%s AND lower(course)=lower(%s)
                           ORDER BY sort_order ASC, id ASC""", (student_id, course))
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["weight"] = float(r["weight"])
        return rows
    except Exception as e:
        log_error("services.grades.get_grading_weights", e); return []


def store_grading_weights(student_id, course, weights):
    if not config.DB_URL:
        return
    try:
        with db_cursor(commit=True) as cur:
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
    except Exception as e:
        log_error("services.grades.store_grading_weights", e)
