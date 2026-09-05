import concurrent.futures
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..errors import log_error
from ..extensions import csrf, db_cursor
from ..security import login_required, page_login_required, rate_limited
from ..services.analytics import log_event
from ..services.course_colors import ensure_course_colors
from ..services.deadlines import (PERSONAL_ITEM_CATEGORIES, PERSONAL_ITEM_COLORS, add_personal_item, build_study_plan,
                                   delete_personal_item, detect_deadline_conflicts, extract_deadlines,
                                   get_all_deadlines, get_upcoming_deadlines, insert_deadlines, set_deadline_completed,
                                   set_deadline_status, update_personal_item)
from ..services.documents import get_docs
from ..services.cron import cron_job
from ..services.email import send_email

bp = Blueprint("calendar", __name__)

MAX_REPROCESS_WORKERS = 5


@bp.route("/deadlines")
@login_required
def deadlines():
    s = g.student
    try:
        days = int(request.args.get("days", 14))
    except (TypeError, ValueError):
        return jsonify({"error": "days must be an integer"}), 400
    if days < 0:
        return jsonify({"error": "days must not be negative"}), 400
    days = min(days, 90)
    return jsonify({"deadlines": get_upcoming_deadlines(s["id"], days)})


@bp.route("/calendar-page")
@page_login_required
def calendar_page():
    s = g.student
    docs = get_docs(s["id"])
    course_names = sorted({(d.get("course") or "").strip() for d in docs
                            if (d.get("course") or "").strip()}, key=str.lower)
    course_colors = ensure_course_colors(s["id"], course_names)
    log_event(s["id"], "page_view", {"page": "calendar"})
    return render_template("calendar.html", s=s, admin_email=config.ADMIN_EMAIL,
                           active="calendar", course_colors=course_colors,
                           personal_item_categories=PERSONAL_ITEM_CATEGORIES,
                           personal_item_colors=PERSONAL_ITEM_COLORS)


@bp.route("/calendar-data")
@login_required
def calendar_data():
    s = g.student
    return jsonify({"deadlines": get_all_deadlines(s["id"])})


@bp.route("/deadlines/<int:deadline_id>/confirm", methods=["POST"])
@login_required
def confirm_deadline(deadline_id):
    s = g.student
    try:
        data = request.get_json(silent=True) or {}
        status = data.get("status", "confirmed")
        updated = set_deadline_status(
            deadline_id, s["id"], status,
            title=data.get("title"), due_date=data.get("due_date"),
        )
        if not updated:
            return jsonify({"error": "Deadline not found"}), 404
        if updated.get("due_date"):
            updated["due_date"] = updated["due_date"].isoformat()
        log_event(s["id"], "deadline_status_changed", {"deadline_id": deadline_id, "status": status})
        return jsonify(updated)
    except Exception as e:
        log_error("calendar.confirm_deadline", e, deadline_id=deadline_id)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/deadlines/<int:deadline_id>/complete", methods=["POST"])
@login_required
def toggle_deadline_completed(deadline_id):
    s = g.student
    try:
        data = request.get_json(silent=True) or {}
        completed = bool(data.get("completed"))
        updated = set_deadline_completed(deadline_id, s["id"], completed)
        if not updated:
            return jsonify({"error": "Deadline not found"}), 404
        log_event(s["id"], "deadline_completed_toggled", {"deadline_id": deadline_id, "completed": completed})
        return jsonify({"success": True, "deadlines": get_all_deadlines(s["id"])})
    except Exception as e:
        log_error("calendar.toggle_deadline_completed", e, deadline_id=deadline_id)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/personal-items", methods=["POST"])
@login_required
def add_personal_item_route():
    s = g.student
    try:
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()[:200]
        category = (data.get("category") or "").strip()
        due_date = (data.get("due_date") or "").strip()
        color = (data.get("color") or "").strip() or None
        frequency = (data.get("frequency") or "").strip() or None
        recurrence_end = (data.get("recurrence_end") or "").strip() or None

        if not title or not due_date:
            return jsonify({"error": "Title and date are required."}), 400
        if category not in PERSONAL_ITEM_CATEGORIES:
            return jsonify({"error": "Please choose a valid category."}), 400
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Invalid date."}), 400
        if frequency and frequency not in ("daily", "weekly", "monthly"):
            return jsonify({"error": "Invalid recurrence."}), 400
        if frequency and not recurrence_end:
            return jsonify({"error": "Recurring items need an end date."}), 400
        if recurrence_end:
            try:
                if datetime.strptime(recurrence_end, "%Y-%m-%d") < datetime.strptime(due_date, "%Y-%m-%d"):
                    return jsonify({"error": "End date can't be before the start date."}), 400
            except ValueError:
                return jsonify({"error": "Invalid end date."}), 400

        ids = add_personal_item(s["id"], title, due_date, category, color, frequency, recurrence_end)
        if not ids:
            return jsonify({"error": "Could not save — please try again."}), 500
        log_event(s["id"], "personal_item_added",
                  {"category": category, "count": len(ids), "recurring": bool(frequency)})
        return jsonify({"success": True, "count": len(ids), "deadlines": get_all_deadlines(s["id"])})
    except Exception as e:
        log_error("calendar.add_personal_item_route", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/personal-items/<int:deadline_id>", methods=["PUT"])
@login_required
def update_personal_item_route(deadline_id):
    s = g.student
    try:
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()[:200] or None
        category = (data.get("category") or "").strip() or None
        due_date = (data.get("due_date") or "").strip() or None
        color = (data.get("color") or "").strip() or None
        apply_to_series = bool(data.get("apply_to_series"))

        if category is not None and category not in PERSONAL_ITEM_CATEGORIES:
            return jsonify({"error": "Please choose a valid category."}), 400
        if due_date is not None:
            try:
                datetime.strptime(due_date, "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "Invalid date."}), 400

        updated = update_personal_item(deadline_id, s["id"], title=title, due_date=due_date,
                                       category=category, color=color, apply_to_series=apply_to_series)
        if not updated:
            return jsonify({"error": "Item not found."}), 404
        log_event(s["id"], "personal_item_edited", {"deadline_id": deadline_id, "series": apply_to_series})
        return jsonify({"success": True, "deadlines": get_all_deadlines(s["id"])})
    except Exception as e:
        log_error("calendar.update_personal_item_route", e, deadline_id=deadline_id)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/personal-items/<int:deadline_id>", methods=["DELETE"])
@login_required
def delete_personal_item_route(deadline_id):
    s = g.student
    try:
        delete_series = request.args.get("series") == "true"
        deleted = delete_personal_item(deadline_id, s["id"], delete_series)
        if not deleted:
            return jsonify({"error": "Item not found."}), 404
        log_event(s["id"], "personal_item_deleted", {"deadline_id": deadline_id, "series": delete_series})
        return jsonify({"success": True, "deadlines": get_all_deadlines(s["id"])})
    except Exception as e:
        log_error("calendar.delete_personal_item_route", e, deadline_id=deadline_id)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/deadline-conflicts")
@login_required
def deadline_conflicts():
    s = g.student
    return jsonify({"conflicts": detect_deadline_conflicts(s["id"])})


@bp.route("/study-plan")
@login_required
def study_plan():
    s = g.student
    weeks = request.args.get("weeks", 4, type=int)
    return jsonify({"weeks": build_study_plan(s["id"], weeks_ahead=max(1, min(weeks, 12)))})


@bp.route("/reprocess-deadlines", methods=["POST"])
@login_required
def reprocess_deadlines():
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    if rate_limited(f"reprocess:{s['id']}", max_calls=3, window_seconds=300):
        return jsonify({"error": "Please wait a few minutes before doing this again."}), 429
    try:
        docs = get_docs(s["id"])
        docs_with_content = [d for d in docs if (d.get("content") or "").strip()]

        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_REPROCESS_WORKERS) as pool:
            future_to_doc = {
                pool.submit(extract_deadlines, (d.get("content") or "").strip()): d
                for d in docs_with_content
            }
            for future in concurrent.futures.as_completed(future_to_doc):
                d = future_to_doc[future]
                try:
                    results[d["id"]] = future.result()
                except Exception as e:
                    log_error("calendar.reprocess_deadlines_extract", e, doc_id=d['id'])
                    results[d["id"]] = []

        total_found = 0
        docs_processed = 0
        docs_skipped_empty = 0
        for d in docs_with_content:
            found = results.get(d["id"], [])
            if not found:
                docs_skipped_empty += 1
                continue
            with db_cursor(commit=True) as cur:
                cur.execute("DELETE FROM deadlines WHERE document_id=%s", (d["id"],))
            insert_deadlines(s["id"], d["id"], d["course"], found)
            docs_processed += 1
            total_found += len(found)
        log_event(s["id"], "deadlines_reprocessed", {"docs": docs_processed, "found": total_found, "skipped_empty": docs_skipped_empty})
        return jsonify({"success": True, "documents_processed": docs_processed, "deadlines_found": total_found})
    except Exception as e:
        log_error("calendar.reprocess_deadlines", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/send-deadline-reminders", methods=["POST"])
@csrf.exempt
@cron_job("send_deadline_reminders")
def send_deadline_reminders(run_id):
    with db_cursor() as cur:
        cur.execute("""SELECT d.id, d.title, d.due_date, d.course, s.id as sid, s.email, s.first_name
                       FROM deadlines d JOIN students s ON s.id = d.student_id
                       WHERE d.reminded = FALSE
                       AND d.due_date BETWEEN (NOW() AT TIME ZONE %s)::date
                                       AND (NOW() AT TIME ZONE %s)::date + 3
                       AND s.is_active IS TRUE
                       AND s.account_deleted_at IS NULL
                       ORDER BY s.id, d.due_date""", (config.APP_TIMEZONE, config.APP_TIMEZONE))
        rows = [dict(r) for r in cur.fetchall()]

    by_student = {}
    for r in rows:
        by_student.setdefault(r["sid"], {"email": r["email"], "first_name": r["first_name"], "items": []})
        by_student[r["sid"]]["items"].append(r)

    sent_count = 0
    failed_count = 0
    reminded_ids = []
    for sid, info in by_student.items():
        lines = [f"  • {it['title']} ({it['course']}) — due {it['due_date'].strftime('%A, %b %d')}"
                 for it in info["items"]]
        body = (f"Hi {info['first_name']},\n\nHere's what's coming up in the next few days:\n\n"
                + "\n".join(lines) + "\n\n— WINK")
        if send_email(info["email"], "Upcoming deadlines — WINK", body):
            sent_count += 1
            reminded_ids.extend(it["id"] for it in info["items"])
        else:
            failed_count += 1

    if reminded_ids:
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE deadlines SET reminded=TRUE WHERE id = ANY(%s)", (reminded_ids,))

    return {
        "number_processed": len(by_student), "number_sent": sent_count, "number_failed": failed_count,
        "students_notified": sent_count, "deadlines_covered": len(rows),
    }


def _weekly_digest_already_ran():
    with db_cursor() as cur:
        cur.execute("""SELECT COUNT(*) as n FROM cron_runs
                       WHERE job_name='send_weekly_digest' AND completed_at IS NOT NULL
                       AND last_error IS NULL AND completed_at > NOW() - INTERVAL '6 days'""")
        already_ran = cur.fetchone()["n"] > 0
    return "Weekly digest already sent within the last 6 days." if already_ran else None


@bp.route("/send-weekly-digest", methods=["POST"])
@csrf.exempt
@cron_job("send_weekly_digest", skip_check=_weekly_digest_already_ran)
def send_weekly_digest(run_id):
    """A once-a-week 'here's what's due this week' email, separate from the
    closer 3-day reminder above — meant to run once, at the start of the
    week (e.g. Monday morning). Unlike the 3-day reminder, this doesn't mark
    individual deadlines as handled (there's no equivalent of `reminded` to
    set — the same deadline is expected to appear here once, then again in
    the closer reminder as it approaches). Because of that, this route
    guards against accidentally running twice in the same week itself
    (see _weekly_digest_already_ran, passed to @cron_job as skip_check),
    rather than relying on per-deadline state."""
    # Monday-to-Sunday window for "this week," in the app's configured
    # timezone rather than the DB server's — same reasoning as every
    # other date comparison in this file.
    local_today = datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()
    week_start = local_today - timedelta(days=local_today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)  # Sunday

    with db_cursor() as cur:
        cur.execute("""SELECT d.id, d.title, d.due_date, d.course, s.id as sid, s.email, s.first_name
                       FROM deadlines d JOIN students s ON s.id = d.student_id
                       WHERE d.due_date BETWEEN %s AND %s
                       AND s.is_active IS TRUE
                       AND s.account_deleted_at IS NULL
                       ORDER BY s.id, d.due_date""", (week_start, week_end))
        rows = [dict(r) for r in cur.fetchall()]

    by_student = {}
    for r in rows:
        by_student.setdefault(r["sid"], {"email": r["email"], "first_name": r["first_name"], "items": []})
        by_student[r["sid"]]["items"].append(r)

    sent_count = 0
    failed_count = 0
    for sid, info in by_student.items():
        lines = [f"  • {it['title']} ({it['course']}) — due {it['due_date'].strftime('%A, %b %d')}"
                 for it in info["items"]]
        body = (f"Hi {info['first_name']},\n\nHere's what's on your plate this week "
                f"({week_start.strftime('%b %d')}–{week_end.strftime('%b %d')}):\n\n"
                + "\n".join(lines) +
                "\n\nYou'll also get a closer reminder as each one approaches.\n\n— WINK")
        if send_email(info["email"], "Your week ahead — WINK", body):
            sent_count += 1
        else:
            failed_count += 1

    return {
        "number_processed": len(by_student), "number_sent": sent_count, "number_failed": failed_count,
        "students_notified": sent_count, "deadlines_covered": len(rows),
    }
