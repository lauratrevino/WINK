from .. import config
from ..errors import log_error
from ..extensions import get_db
from .course_colors import ensure_course_colors


def _active_days_and_questions(cur, student_id, days_back):
    cur.execute("""
        SELECT COUNT(DISTINCT DATE(created_at)) as active_days,
               COUNT(*) FILTER (WHERE event_type = 'question_asked') as questions_asked
        FROM events
        WHERE student_id = %s AND created_at >= NOW() - (%s * INTERVAL '1 day')
    """, (student_id, days_back))
    row = cur.fetchone()
    return row["active_days"] or 0, row["questions_asked"] or 0


def _completion_stats(cur, student_id):
    cur.execute("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE completed) as completed,
               COUNT(*) FILTER (WHERE completed AND completed_at IS NOT NULL
                                 AND completed_at::date <= due_date) as on_time
        FROM deadlines
        WHERE student_id = %s AND due_date IS NOT NULL
    """, (student_id,))
    return dict(cur.fetchone())


def _organization_stats(cur, student_id, days_back):
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE is_personal) as personal_items_added,
               COUNT(*) FILTER (WHERE NOT is_personal AND status IN ('confirmed', 'corrected')) as deadlines_reviewed
        FROM deadlines
        WHERE student_id = %s AND created_at >= NOW() - (%s * INTERVAL '1 day')
    """, (student_id, days_back))
    return dict(cur.fetchone())


def _practice_stats(cur, student_id):
    cur.execute("""
        SELECT COUNT(*) as total_questions,
               COUNT(*) FILTER (WHERE last_attempted_at IS NOT NULL) as attempted,
               COALESCE(AVG(correct_streak) FILTER (WHERE last_attempted_at IS NOT NULL), 0) as avg_streak
        FROM practice_questions
        WHERE student_id = %s
    """, (student_id,))
    row = dict(cur.fetchone())
    row["avg_streak"] = round(float(row["avg_streak"] or 0), 1)
    return row


def _daily_activity(cur, student_id, days_back=14):
    cur.execute("""
        SELECT DATE_TRUNC('day', created_at)::date as day, COUNT(*) as n
        FROM events
        WHERE student_id = %s AND created_at >= NOW() - (%s * INTERVAL '1 day')
        GROUP BY day
        ORDER BY day ASC
    """, (student_id, days_back))
    return [{"date": r["day"].isoformat(), "count": r["n"]} for r in cur.fetchall()]


def _weekly_activity(cur, student_id, weeks_back=6):
    cur.execute("""
        SELECT DATE_TRUNC('week', created_at)::date as week_start, COUNT(*) as n
        FROM events
        WHERE student_id = %s AND created_at >= NOW() - (%s * INTERVAL '1 week')
        GROUP BY week_start
        ORDER BY week_start ASC
    """, (student_id, weeks_back))
    return [{"week_start": r["week_start"].isoformat(), "count": r["n"]} for r in cur.fetchall()]


def _monthly_activity(cur, student_id, months_back=6):
    cur.execute("""
        SELECT DATE_TRUNC('month', created_at)::date as month_start, COUNT(*) as n
        FROM events
        WHERE student_id = %s AND created_at >= NOW() - (%s * INTERVAL '1 month')
        GROUP BY month_start
        ORDER BY month_start ASC
    """, (student_id, months_back))
    return [{"month": r["month_start"].strftime("%Y-%m"), "count": r["n"]} for r in cur.fetchall()]


def _weekly_questions_this_vs_last(cur, student_id):
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE event_type = 'question_asked'
                              AND created_at >= DATE_TRUNC('week', NOW())) as this_week,
            COUNT(*) FILTER (WHERE event_type = 'question_asked'
                              AND created_at >= DATE_TRUNC('week', NOW()) - INTERVAL '7 days'
                              AND created_at < DATE_TRUNC('week', NOW())) as last_week
        FROM events WHERE student_id = %s
    """, (student_id,))
    row = cur.fetchone()
    return row["this_week"] or 0, row["last_week"] or 0


def _completed_this_week(cur, student_id):
    cur.execute("""
        SELECT COUNT(*) as n FROM deadlines
        WHERE student_id = %s AND completed AND completed_at >= DATE_TRUNC('week', NOW())
    """, (student_id,))
    return cur.fetchone()["n"] or 0


def _generate_noticed_insights(this_week_q, last_week_q, completed_this_week):
    notes = []
    if last_week_q and this_week_q > last_week_q:
        notes.append(f"You've asked WINK {this_week_q - last_week_q} more question(s) this week than last week.")
    elif this_week_q == 0 and last_week_q > 0:
        notes.append("It's been quiet this week — WINK's here whenever you need it.")
    elif this_week_q > 0 and last_week_q == 0:
        notes.append("You're off to a good start asking WINK questions this week!")
    if completed_this_week >= 3:
        notes.append(f"You've checked off {completed_this_week} item(s) as complete this week — nice work staying on top of things.")
    return notes[:3]


def _by_course_breakdown(cur, student_id):
    cur.execute("""
        SELECT course,
               COUNT(*) as total,
               COUNT(*) FILTER (WHERE completed) as completed,
               COUNT(*) FILTER (WHERE completed AND completed_at IS NOT NULL
                                 AND completed_at::date <= due_date) as on_time
        FROM deadlines
        WHERE student_id = %s AND due_date IS NOT NULL AND is_personal IS NOT TRUE
        GROUP BY course
    """, (student_id,))
    by_course = {r["course"]: {"course": r["course"], "total": r["total"], "completed": r["completed"],
                                "on_time": r["on_time"], "practice_total": 0, "practice_attempted": 0,
                                "avg_streak": 0.0}
                 for r in cur.fetchall()}

    cur.execute("""
        SELECT course,
               COUNT(*) as total_questions,
               COUNT(*) FILTER (WHERE last_attempted_at IS NOT NULL) as attempted,
               COALESCE(AVG(correct_streak) FILTER (WHERE last_attempted_at IS NOT NULL), 0) as avg_streak
        FROM practice_questions
        WHERE student_id = %s
        GROUP BY course
    """, (student_id,))
    for r in cur.fetchall():
        row = by_course.setdefault(r["course"], {"course": r["course"], "total": 0, "completed": 0,
                                                   "on_time": 0, "practice_total": 0,
                                                   "practice_attempted": 0, "avg_streak": 0.0})
        row["practice_total"] = r["total_questions"]
        row["practice_attempted"] = r["attempted"]
        row["avg_streak"] = round(float(r["avg_streak"] or 0), 1)

    if not by_course:
        return []
    colors = ensure_course_colors(student_id, list(by_course.keys()))
    result = list(by_course.values())
    for row in result:
        row["color"] = colors.get(row["course"], "#94A3B8")
    result.sort(key=lambda r: r["course"].lower())
    return result


def get_progress_summary(student_id, days_back=30):
    empty = {
        "active_days": 0, "questions_asked": 0,
        "completion": {"total": 0, "completed": 0, "on_time": 0},
        "organization": {"personal_items_added": 0, "deadlines_reviewed": 0},
        "practice": {"total_questions": 0, "attempted": 0, "avg_streak": 0},
        "daily_activity": [],
        "weekly_activity": [],
        "monthly_activity": [],
        "noticed": [],
        "by_course": [],
    }
    if not config.DB_URL:
        return empty
    try:
        conn = get_db(); cur = conn.cursor()
        active_days, questions_asked = _active_days_and_questions(cur, student_id, days_back)
        completion = _completion_stats(cur, student_id)
        organization = _organization_stats(cur, student_id, days_back)
        practice = _practice_stats(cur, student_id)
        daily = _daily_activity(cur, student_id)
        weekly = _weekly_activity(cur, student_id)
        monthly = _monthly_activity(cur, student_id)
        this_week_q, last_week_q = _weekly_questions_this_vs_last(cur, student_id)
        completed_this_week = _completed_this_week(cur, student_id)
        by_course = _by_course_breakdown(cur, student_id)
        cur.close()

        noticed = _generate_noticed_insights(this_week_q, last_week_q, completed_this_week)

        return {
            "active_days": active_days,
            "questions_asked": questions_asked,
            "completion": completion,
            "organization": organization,
            "practice": practice,
            "daily_activity": daily,
            "weekly_activity": weekly,
            "monthly_activity": monthly,
            "noticed": noticed,
            "by_course": by_course,
        }
    except Exception as e:
        log_error("services.progress.get_progress_summary", e)
        return empty


def _trend_label(recent, previous):
    if recent > previous:
        return "up"
    if recent < previous:
        return "down"
    return "steady"


def get_student_progress(student_id, days_back=30):
    summary = get_progress_summary(student_id, days_back)
    snapshot = {
        "active_days": summary["active_days"],
        "deadlines_completed": summary["completion"]["completed"],
        "questions": summary["questions_asked"],
    }

    trend = "steady"
    if config.DB_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE is_personal AND created_at >= NOW() - (7 * INTERVAL '1 day')) as recent,
                    COUNT(*) FILTER (WHERE is_personal AND created_at >= NOW() - (14 * INTERVAL '1 day')
                                      AND created_at < NOW() - (7 * INTERVAL '1 day')) as previous
                FROM deadlines WHERE student_id = %s
            """, (student_id,))
            row = cur.fetchone()
            cur.close()
            if row:
                trend = _trend_label(row["recent"] or 0, row["previous"] or 0)
        except Exception as e:
            log_error("services.progress.get_student_progress_trend", e)

    return {
        "snapshot": snapshot,
        "habits": {"planning": {"trend": trend}},
    }
