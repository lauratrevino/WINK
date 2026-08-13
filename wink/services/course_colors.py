from .. import config
from ..errors import log_error
from ..extensions import get_db

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
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT course_normalized, color FROM course_colors WHERE student_id=%s",
                    (student_id,))
        existing = {r["course_normalized"]: r["color"] for r in cur.fetchall()}
        used_colors = set(existing.values())

        result = {}
        newly_assigned = False
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
                newly_assigned = True
            result[name] = color
        if newly_assigned:
            conn.commit()
        cur.close()
        return result
    except Exception as e:
        log_error("services.course_colors.ensure_course_colors", e)
        return {}


def release_color_if_course_gone(student_id, course_name):
    if not config.DB_URL:
        return
    normalized = _normalize(course_name)
    if not normalized:
        return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM documents WHERE student_id=%s AND lower(trim(course))=%s LIMIT 1",
            (student_id, normalized),
        )
        if cur.fetchone():
            cur.close()
            return  
        cur.execute(
            "DELETE FROM course_colors WHERE student_id=%s AND course_normalized=%s",
            (student_id, normalized),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        log_error("services.course_colors.release_color_if_course_gone", e)
