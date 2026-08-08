"""Persistent, student-scoped course -> color assignment.

Unlike a stateless hash or a list-position scheme (both of which either
can't guarantee uniqueness, or reshuffle every course's color whenever the
course list changes), this stores one real row per (student, course) in the
`course_colors` table. A color is assigned the first time a course is seen
and stays fixed — same course, same color, forever — until that course's
last document is deleted, at which point release_color_if_course_gone()
below deletes the row and frees the color for a future new course to reuse.

The DB-level UNIQUE(student_id, color) constraint is what actually
guarantees no two of one student's active courses ever share a color —
not application logic that could drift out of sync with the data.

IMPORTANT: WINK_COURSE_COLOR_PALETTE here must stay identical to the one in
static/js/course-colors.js — this module is the source of truth for which
color a course gets, but the JS still needs the same palette values to
render swatches, since the DB only stores a hex string.
"""
from .. import config
from ..errors import log_error
from ..extensions import get_db

WINK_COURSE_COLOR_PALETTE = [
    '#FF8200',  # UTEP orange
    '#0EA5E9',  # sky blue
    '#22C55E',  # green
    '#A855F7',  # purple
    '#EF4444',  # red
    '#14B8A6',  # teal
    '#EAB308',  # amber
    '#EC4899',  # pink
    '#6366F1',  # indigo
    '#84CC16',  # lime
    '#F97316',  # burnt orange
    '#06B6D4',  # cyan
]


def _normalize(course_name):
    return (course_name or "").strip().lower()


def _hash_fallback_color(normalized):
    """Only reached if every palette color is already taken by this
    student (more than len(WINK_COURSE_COLOR_PALETTE) distinct active
    courses) — a deterministic pick so at least it's stable within a
    request, rather than erroring. At that point a color repeat is
    unavoidable with a fixed-size palette."""
    h = 5381
    for ch in normalized:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return WINK_COURSE_COLOR_PALETTE[h % len(WINK_COURSE_COLOR_PALETTE)]


def ensure_course_colors(student_id, course_names):
    """Makes sure every name in course_names (raw display names, e.g. from
    get_docs()) has a color assigned to this student, assigning new ones as
    needed, and returns {display_name: hex_color}. Cheap no-op for courses
    that already have a color. Call this from any route that renders
    course colors (documents, calendar, dashboard) — it's the only place
    new assignments happen."""
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
            # First time we've seen this course for this student — take the
            # first palette color not currently in use (this is what makes
            # a freed-up color from a deleted course available again: it's
            # simply not in used_colors anymore).
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
    """Call after deleting a document. If the student has no remaining
    documents for this course, deletes its course_colors row so the color
    becomes available for a future new course. No-op (and safe to call
    unconditionally) if the course still has other documents, or never had
    a color assigned in the first place."""
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
            return  # course still has other documents — keep its color
        cur.execute(
            "DELETE FROM course_colors WHERE student_id=%s AND course_normalized=%s",
            (student_id, normalized),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        log_error("services.course_colors.release_color_if_course_gone", e)
