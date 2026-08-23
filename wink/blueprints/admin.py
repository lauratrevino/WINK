
from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..errors import log_error
from ..extensions import generate_csrf_token, db_cursor
from ..security import admin_page_required, admin_required
from ..services.analytics import (anonymize_student_record, compute_engagement_insights,
                                   get_demo_usage_stats, get_student_summaries,
                                   get_total_token_usage, log_event, safe_payload)
from ..services.health import run_health_checks, overall_status

bp = Blueprint("admin", __name__)


@bp.route("/analytics-page")
@admin_page_required
def analytics_page():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "analytics"})
        return render_template("analytics.html", s=s, active="analytics")
    except Exception as e:
        log_error("admin.analytics_page", e)
        return f"<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><input type='hidden' name='csrf_token' value='{generate_csrf_token()}'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/analytics-data")
@admin_required
def analytics_data():
    try:
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM students WHERE is_demo IS NOT TRUE")
            total_s = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type IN ('login','account_created')")
            total_sess = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='question_asked'")
            total_q = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='file_uploaded'")
            total_up = cur.fetchone()["n"]

            students = get_student_summaries(cur)

            cur.execute("""
                SELECT
                    e.id, e.event_type, e.payload,
                    to_char(e.created_at, 'Mon DD HH24:MI') as ts,
                    s.first_name, s.last_name, s.email
                FROM events e
                LEFT JOIN students s ON s.id = e.student_id
                ORDER BY e.created_at DESC
                LIMIT 60
            """)
            recent = []
            for r in cur.fetchall():
                row = dict(r)
                row["payload"] = safe_payload(row.get("payload"))
                recent.append(row)

            cur.execute("SELECT major, COUNT(*) as n FROM students WHERE is_demo IS NOT TRUE GROUP BY major ORDER BY n DESC")
            by_major = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT classification, COUNT(*) as n FROM students WHERE is_demo IS NOT TRUE GROUP BY classification ORDER BY n DESC")
            by_class = [dict(r) for r in cur.fetchall()]

            token_totals = get_total_token_usage(cur)

        return jsonify({
            "total_students": total_s,
            "total_sessions": total_sess,
            "total_questions": total_q,
            "total_uploads": total_up,
            "students": students,
            "recent": recent,
            "by_major": by_major,
            "by_class": by_class,
            **token_totals,
        })
    except Exception as e:
        log_error("admin.analytics_data", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/analytics-data-full")
@admin_required
def analytics_data_full():
    try:
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM students WHERE is_demo IS NOT TRUE"); total_s = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type IN ('login','account_created')")
            total_sess = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='question_asked'"); total_q = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='file_uploaded'"); total_up = cur.fetchone()["n"]

            students = get_student_summaries(cur)

            cur.execute("""
                SELECT e.payload, to_char(e.created_at,'Mon DD HH24:MI') as ts,
                       s.first_name, s.last_name, s.email
                FROM events e LEFT JOIN students s ON s.id=e.student_id
                WHERE e.event_type = 'question_asked'
                ORDER BY e.created_at DESC LIMIT 100""")
            questions = []
            for r in cur.fetchall():
                row = dict(r)
                p = safe_payload(row.pop("payload"))
                questions.append({
                    "first_name": row.get("first_name", ""),
                    "last_name": row.get("last_name", ""),
                    "email": row.get("email", ""),
                    "question": p.get("q", ""),
                    "ts": row.get("ts", "")
                })

            # Reads from answer_logs rather than pairing up question_asked/
            # answer_given events — same reasoning as student_conversations()
            # above: answer_logs has the full question+answer together on one
            # row, and it's the only place that still holds the full answer
            # text. (The separate "questions" feed above intentionally keeps
            # reading the short 200-char snippet from events — that field
            # serves a distinct purpose, exact-match grouping for the "common
            # questions" analytics feature, not a full-text display.)
            cur.execute("""
                SELECT al.question, al.answer_text, to_char(al.created_at,'Mon DD HH24:MI') as ts,
                       s.first_name, s.last_name, s.email, s.id as sid
                FROM answer_logs al LEFT JOIN students s ON s.id = al.student_id
                ORDER BY s.id, al.created_at ASC LIMIT 200""")
            conversations = [
                {
                    "first_name": r.get("first_name", ""),
                    "last_name": r.get("last_name", ""),
                    "email": r.get("email", ""),
                    "question": r.get("question", ""),
                    "answer": r.get("answer_text", ""),
                    "ts": r.get("ts", ""),
                    "sid": r.get("sid"),
                }
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT e.event_type, e.payload, to_char(e.created_at,'Mon DD HH24:MI') as ts,
                       s.first_name, s.last_name, s.email
                FROM events e LEFT JOIN students s ON s.id=e.student_id
                ORDER BY e.created_at DESC LIMIT 100""")
            recent = []
            for r in cur.fetchall():
                row = dict(r)
                row["payload"] = safe_payload(row.get("payload"))
                recent.append(row)

            cur.execute("SELECT major, COUNT(*) as n FROM students WHERE is_demo IS NOT TRUE GROUP BY major ORDER BY n DESC")
            by_major = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT classification, COUNT(*) as n FROM students WHERE is_demo IS NOT TRUE GROUP BY classification ORDER BY n DESC")
            by_class = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT course, COUNT(*) as n FROM documents GROUP BY course ORDER BY n DESC")
            by_course = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT to_char(created_at,'Mon DD') as day, COUNT(*) as n
                FROM events
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY to_char(created_at,'Mon DD'), DATE(created_at)
                ORDER BY DATE(created_at) ASC""")
            daily = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT d.title, d.course, d.due_date, s.first_name, s.last_name
                FROM deadlines d JOIN students s ON s.id = d.student_id
                WHERE d.due_date >= (NOW() AT TIME ZONE %s)::date
                ORDER BY d.due_date ASC LIMIT 100""", (config.APP_TIMEZONE,))
            upcoming_deadlines = []
            for r in cur.fetchall():
                row = dict(r)
                row["due_date"] = row["due_date"].isoformat() if row["due_date"] else None
                upcoming_deadlines.append(row)
            cur.execute("SELECT COUNT(*) as n FROM deadlines WHERE due_date >= (NOW() AT TIME ZONE %s)::date", (config.APP_TIMEZONE,))
            total_deadlines = cur.fetchone()["n"]

            insights = compute_engagement_insights(cur)
            token_totals = get_total_token_usage(cur)
            demo_usage = get_demo_usage_stats(cur)

        return jsonify({
            "total_students": total_s,
            "total_sessions": total_sess,
            "total_questions": total_q,
            "total_uploads": total_up,
            "total_deadlines": total_deadlines,
            "students": students,
            "questions": questions,
            "conversations": conversations,
            "recent": recent,
            "by_major": by_major,
            "by_class": by_class,
            "by_course": by_course,
            "daily": daily,
            "upcoming_deadlines": upcoming_deadlines,
            "demo_usage": demo_usage,
            **insights,
            **token_totals,
        })
    except Exception as e:
        log_error("admin.analytics_data_full", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/student-conversations/<int:sid>")
@admin_required
def student_conversations(sid):
    try:
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        with db_cursor() as cur:
            # Reads from answer_logs rather than reconstructing from paired
            # question_asked/answer_given events — answer_logs already has the
            # full question and answer together on one row per exchange (no
            # fragile sequential pairing needed), and it's the only place that
            # still holds the full answer text (see the comment in chat.py's
            # log_event("answer_given", ...) call for why).
            cur.execute("""
                SELECT question, answer_text, to_char(created_at,'Mon DD HH24:MI') as ts
                FROM answer_logs
                WHERE student_id=%s
                ORDER BY created_at ASC""", (sid,))
            conversations = [
                {"question": r["question"], "answer": r["answer_text"], "ts": r["ts"]}
                for r in cur.fetchall()
            ]
        return jsonify({"conversations": conversations})
    except Exception as e:
        log_error("admin.student_conversations", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/toggle-student-active", methods=["POST"])
@admin_required
def toggle_student_active():
    try:
        s = g.student
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        data = request.get_json() or {}
        target_id = data.get("student_id")
        if not target_id:
            return jsonify({"error": "Missing student_id"}), 400
        if str(target_id) == str(s["id"]):
            return jsonify({"error": "You can't suspend your own admin account."}), 400
        with db_cursor(commit=True) as cur:
            cur.execute("SELECT id, is_active FROM students WHERE id=%s", (target_id,))
            target = cur.fetchone()
            if not target:
                return jsonify({"error": "Student not found"}), 404
            new_active = not target["is_active"]
            cur.execute("UPDATE students SET is_active=%s WHERE id=%s", (new_active, target_id))
        log_event(s["id"], "student_suspended" if not new_active else "student_reactivated", {"target_id": target_id})
        return jsonify({"success": True, "is_active": new_active})
    except Exception as e:
        log_error("admin.toggle_student_active", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/anonymize-student", methods=["POST"])
@admin_required
def anonymize_student():
    """Manually triggered, no fixed schedule — see anonymize_student_record()
    in services/analytics.py (shared with a student's own account deletion)
    for exactly what this does and doesn't scrub."""
    try:
        s = g.student
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        data = request.get_json() or {}
        target_id = data.get("student_id")
        if not target_id:
            return jsonify({"error": "Missing student_id"}), 400
        if str(target_id) == str(s["id"]):
            return jsonify({"error": "You can't anonymize your own admin account."}), 400
        with db_cursor() as cur:
            cur.execute("SELECT id, anonymized_at FROM students WHERE id=%s", (target_id,))
            target = cur.fetchone()
        if not target:
            return jsonify({"error": "Student not found"}), 404
        if target["anonymized_at"]:
            return jsonify({"error": "This student has already been anonymized."}), 400
        label = anonymize_student_record(target_id)
        if not label:
            return jsonify({"error": "Could not anonymize this student."}), 500
        log_event(s["id"], "student_anonymized", {"target_id": target_id})
        return jsonify({"success": True, "label": label})
    except Exception as e:
        log_error("admin.anonymize_student", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/delete-student", methods=["POST"])
@admin_required
def delete_student():
    """Permanently and irreversibly removes a student row from the database
    (not the same as Anonymize, which keeps a scrubbed record for research
    retention). Every table with a foreign key to students.id cascades on
    delete except events/document_chunks, which have no FK constraint and
    are simply left with a dangling student_id — harmless, but a reminder
    this is a real delete, not a soft one.

    Intended for test/junk accounts (e.g. so a real email can be reused to
    register again), NOT for real pilot participants — use Anonymize for
    those to honor the research data retention commitment in the consent
    form. As an extra guard against fat-fingering a real student, the
    caller must echo back the target's exact email.
    """
    try:
        s = g.student
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        data = request.get_json() or {}
        target_id = data.get("student_id")
        confirm_email = (data.get("confirm_email") or "").strip().lower()
        if not target_id:
            return jsonify({"error": "Missing student_id"}), 400
        if str(target_id) == str(s["id"]):
            return jsonify({"error": "You can't delete your own admin account."}), 400
        with db_cursor() as cur:
            cur.execute("SELECT id, email FROM students WHERE id=%s", (target_id,))
            target = cur.fetchone()
        if not target:
            return jsonify({"error": "Student not found"}), 404
        if not confirm_email or confirm_email != target["email"].strip().lower():
            return jsonify({"error": "Typed email doesn't match this student's email."}), 400
        with db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM students WHERE id=%s", (target_id,))
        log_event(s["id"], "student_deleted", {"target_id": target_id, "target_email": target["email"]})
        return jsonify({"success": True, "email": target["email"]})
    except Exception as e:
        log_error("admin.delete_student", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/health-data")
@admin_required
def health_data():
    """JSON companion to /health-page — same underlying checks, admin-only
    like the human page (this used to call a separate, disconnected
    health-check implementation that had silently drifted from the one
    powering /health-page; both now share one source of truth in
    services/health.py)."""
    try:
        checks = run_health_checks()
        return jsonify({
            "overall": overall_status(checks),
            "checks": [{"name": name, "status": status, "detail": detail} for name, (status, detail) in checks.items()],
        })
    except Exception as e:
        log_error("admin.health_data", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/email-suppressions")
@admin_required
def email_suppressions():
    """Lists addresses WINK will no longer send to, due to a prior hard
    bounce or spam complaint reported by AWS SES — visible so you can see
    who's affected and, if warranted (e.g. a student fixed a full mailbox),
    manually clear one via the endpoint below."""
    try:
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        with db_cursor() as cur:
            cur.execute("SELECT email, reason, created_at FROM email_suppressions ORDER BY created_at DESC LIMIT 500")
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        return jsonify({"suppressions": rows})
    except Exception as e:
        log_error("admin.email_suppressions", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/email-suppressions/remove", methods=["POST"])
@admin_required
def remove_email_suppression():
    try:
        s = g.student
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        if not email:
            return jsonify({"error": "Missing email"}), 400
        with db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM email_suppressions WHERE email=%s RETURNING email", (email,))
            row = cur.fetchone()
        if not row:
            return jsonify({"error": "That address wasn't on the suppression list."}), 404
        log_event(s["id"], "email_suppression_removed", {"email": email})
        return jsonify({"success": True})
    except Exception as e:
        log_error("admin.remove_email_suppression", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/admin-page")
@admin_page_required
def admin_hub():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "admin_hub"})
        return render_template("admin_hub.html", s=s, active="admin")
    except Exception as e:
        log_error("admin.admin_hub", e)
        return f"<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><input type='hidden' name='csrf_token' value='{generate_csrf_token()}'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500
