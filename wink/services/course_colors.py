from .. import config
from ..errors import log_error
from ..extensions import db_cursor

WINK_COURSE_COLOR_PALETTE = [
    '#FF8200',  
    '#0EA5E9',  
    '#22C55E',  
    '#A855F7',  
    '#EF4444',  
    '#14B8A6',  
    '#EAB308',  
    '#EC4899',  
    '#6366F1',  
    '#84CC16',  
    '#F97316',  
    '#06B6D4',  
]


def _normalize(course_name):
    return (course_name or "").strip().lower()


def _hash_fallback_color(normalized):
    h = 5381
    for ch in normalized:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return WINK_COURSE_COLOR_PALETTE[h % len(WINK_COURSE_COLOR_PALETTE)]


def ensure_course_colors(student_id, course_names):
    if not config.DB_URL:
        return {}
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("SELECT course_normalized, color FROM course_colors WHERE student_id=%s",
                        (student_id,))
            existing = {r["course_normalized"]: r["color"] for r in cur.fetchall()}
            used_colors = set(existing.values())

            result = {}
            for name in course_names:
                normalized = _normalize(name)
                if not normalized:
                    continue
                if normalized in existing:
                    result[name] = existing[normalized]
                    continue
                color = next((c for c in WINK_COURSE_COLOR_PALETTE if c not in used_colors), None)
                if color is None:
                    color = _hash_fallback_color(normalized)
                else:
                    cur.execute(
                        """INSERT INTO course_colors (student_id, course_normalized, course_display, color)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (student_id, course_normalized) DO NOTHING""",
                        (student_id, normalized, name, color),
                    )
                    used_colors.add(color)
                    existing[normalized] = color
                result[name] = color
            return result
    except Exception as e:
        log_error("services.course_colors.ensure_course_colors", e)
        return {}


def release_color_if_course_gone(student_id, course_name):
    """Back-compat wrapper — use purge_course_data_if_gone() for new callers."""
    purge_course_data_if_gone(student_id, course_name)


def purge_course_data_if_gone(student_id, course_name):
    """When the last document for a course is deleted, this clears out every
    other piece of data still tagged with that course name for this student
    (color assignment, deadlines, practice questions) so nothing orphaned is
    left behind to show up on pages like Progress that don't cross-check the
    documents table. Only acts when NO document still references the course —
    if another document for the same course exists, this is a no-op, since
    the course itself hasn't actually gone away."""
    if not config.DB_URL:
        return
    normalized = _normalize(course_name)
    if not normalized:
        return
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "SELECT 1 FROM documents WHERE student_id=%s AND lower(trim(course))=%s LIMIT 1",
                (student_id, normalized),
            )
            if cur.fetchone():
                return
            cur.execute(
                "DELETE FROM course_colors WHERE student_id=%s AND course_normalized=%s",
                (student_id, normalized),
            )
            cur.execute(
                "DELETE FROM deadlines WHERE student_id=%s AND lower(trim(course))=%s AND is_personal IS NOT TRUE",
                (student_id, normalized),
            )
            cur.execute(
                "DELETE FROM practice_questions WHERE student_id=%s AND lower(trim(course))=%s",
                (student_id, normalized),
            )
    except Exception as e:
        log_error("services.course_colors.purge_course_data_if_gone", e)
