
import secrets

from flask import Blueprint, g, jsonify, render_template, request
from werkzeug.security import generate_password_hash

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import admin_page_required, admin_required
from ..services.analytics import compute_engagement_insights, get_demo_usage_stats, get_student_summaries, get_total_token_usage, log_event, safe_payload
from ..services.health import get_health_report

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
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/analytics-data")
@admin_required
def analytics_data():
    try:
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        conn = get_db(); cur = conn.cursor()

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

        cur.close()
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
        conn = get_db(); cur = conn.cursor()

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

        cur.execute("""
            SELECT e.id, e.event_type, e.payload, e.created_at,
                   to_char(e.created_at,'Mon DD HH24:MI') as ts,
                   s.first_name, s.last_name, s.email, s.id as sid
            FROM events e LEFT JOIN students s ON s.id=e.student_id
            WHERE e.event_type IN ('question_asked','answer_given')
            ORDER BY s.id, e.created_at ASC LIMIT 400""")
        raw_events = [dict(r) for r in cur.fetchall()]
        conversations = []
        i = 0
        while i < len(raw_events):
            ev = raw_events[i]
            p = safe_payload(ev.get("payload"))
            if ev["event_type"] == "question_asked":
                conv = {
                    "first_name": ev.get("first_name", ""),
                    "last_name": ev.get("last_name", ""),
                    "email": ev.get("email", ""),
                    "question": p.get("q", ""),
                    "answer": "",
                    "ts": ev.get("ts", ""),
                    "sid": ev.get("sid")
                }
                if i+1 < len(raw_events) and raw_events[i+1]["event_type"] == "answer_given" and raw_events[i+1].get("sid") == ev.get("sid"):
                    ap = safe_payload(raw_events[i+1].get("payload"))
                    conv["answer"] = ap.get("full_answer", "")
                    i += 2
                else:
                    i += 1
                conversations.append(conv)
            else:
                i += 1

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

        cur.close()
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT e.event_type, e.payload, to_char(e.created_at,'Mon DD HH24:MI') as ts
            FROM events e
            WHERE e.student_id=%s AND e.event_type IN ('question_asked','answer_given')
            ORDER BY e.created_at ASC""", (sid,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conversations = []
        i = 0
        while i < len(rows):
            ev = rows[i]
            p = safe_payload(ev.get("payload"))
            if ev["event_type"] == "question_asked":
                conv = {"question": p.get("q", ""), "answer": "", "ts": ev.get("ts", "")}
                if i+1 < len(rows) and rows[i+1]["event_type"] == "answer_given":
                    ap = safe_payload(rows[i+1].get("payload"))
                    conv["answer"] = ap.get("full_answer", "")
                    i += 2
                else:
                    i += 1
                conversations.append(conv)
            else:
                i += 1
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, is_active FROM students WHERE id=%s", (target_id,))
        target = cur.fetchone()
        if not target:
            cur.close()
            return jsonify({"error": "Student not found"}), 404
        new_active = not target["is_active"]
        cur.execute("UPDATE students SET is_active=%s WHERE id=%s", (new_active, target_id))
        conn.commit(); cur.close()
        log_event(s["id"], "student_suspended" if not new_active else "student_reactivated", {"target_id": target_id})
        return jsonify({"success": True, "is_active": new_active})
    except Exception as e:
        log_error("admin.toggle_student_active", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/anonymize-student", methods=["POST"])
@admin_required
def anonymize_student():
    """Manually triggered, no fixed schedule — replaces a student's name and email
    with an opaque, untraceable label, and scrubs any email address left behind
    in that student's own event-log payloads. Irreversible: the original name/
    email are overwritten, not stored anywhere else, so there's no way back once
    this runs. Login is also disabled (password hash randomized) since the
    account can no longer be meaningfully identified by its owner anyway.

    Scope, to be upfront about it: this scrubs the student row and WINK's own
    system-generated event payloads. It does NOT search conversation text or
    document content for a name a student may have typed themselves (e.g. "hi,
    I'm Jane") — that content stays as uploaded/written, since altering it would
    corrupt the research record it exists to preserve."""
    try:
        s = g.student
        if not config.DB_URL: return jsonify({"error": "No database"}), 500
        data = request.get_json() or {}
        target_id = data.get("student_id")
        if not target_id:
            return jsonify({"error": "Missing student_id"}), 400
        if str(target_id) == str(s["id"]):
            return jsonify({"error": "You can't anonymize your own admin account."}), 400
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, anonymized_at FROM students WHERE id=%s", (target_id,))
        target = cur.fetchone()
        if not target:
            cur.close()
            return jsonify({"error": "Student not found"}), 404
        if target["anonymized_at"]:
            cur.close()
            return jsonify({"error": "This student has already been anonymized."}), 400
        code = secrets.token_hex(6)
        cur.execute("""UPDATE students SET first_name=%s, last_name=%s, email=%s,
                       password_hash=%s, anonymized_at=NOW() WHERE id=%s""",
                    ("Anonymized", f"Participant-{code}", f"anon-{code}@anonymized.wink",
                     generate_password_hash(secrets.token_hex(32)), target_id))
        # Scrub any email address left behind in this student's event payloads
        # from before email-in-events was stopped — see the note above about
        # scope: this handles WINK's own past logging, not free-text a student
        # may have typed into a conversation or document.
        cur.execute("""UPDATE events SET payload = (payload::jsonb - 'email')::text
                       WHERE student_id=%s AND payload::jsonb ? 'email'""", (target_id,))
        conn.commit(); cur.close()
        log_event(s["id"], "student_anonymized", {"target_id": target_id})
        return jsonify({"success": True, "label": f"Participant-{code}"})
    except Exception as e:
        log_error("admin.anonymize_student", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/health-page")
@admin_page_required
def health_page():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "health"})
        return render_template("health.html", s=s, active="health")
    except Exception as e:
        log_error("admin.health_page", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/health-data")
@admin_required
def health_data():
    try:
        return jsonify(get_health_report())
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT email, reason, created_at FROM email_suppressions ORDER BY created_at DESC LIMIT 500")
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        cur.close()
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM email_suppressions WHERE email=%s RETURNING email", (email,))
        row = cur.fetchone()
        conn.commit(); cur.close()
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
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500
