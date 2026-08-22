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
            max_tokens=1024,
            system=(
                "Extract the grading/weighting breakdown from the course material given to "
                "you (e.g. a syllabus's 'Grading', 'Course Requirements', or 'Evaluation' "
                "section) — the categories that make up the final grade and each one's "
                "percentage weight.\n"
                "Output exactly one row per category, named and weighted exactly as the "
                "syllabus states it — do NOT split a grouped/repeated category (e.g. 'Market "
                "Labs (4) — 16%', '5 Quizzes at 3% each', '3 Homework Assignments — 20%') "
                "into one row per individual item. Keep it as a single row using the "
                "category's stated name and its combined weight as written (e.g. 'Market "
                "Labs' at 16%, not four separate 'Market Lab' rows at 4% each).\n"
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
        return out[:20]
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
