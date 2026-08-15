
from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..errors import log_error
from ..extensions import generate_csrf_token, get_db
from ..security import login_required, page_login_required
from ..services.analytics import log_event, get_questions_this_month
from ..services.course_colors import ensure_course_colors
from ..services.deadlines import get_upcoming_deadlines
from ..services.documents import get_docs
from ..services.progress import get_student_progress

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
@page_login_required
def dashboard():
    try:
        s = g.student
        docs = get_docs(s["id"])
        course_names = sorted({(d.get("course") or "").strip() for d in docs
                                if (d.get("course") or "").strip()}, key=str.lower)
        course_colors = ensure_course_colors(s["id"], course_names)
        upcoming_deadlines = get_upcoming_deadlines(s["id"], days_ahead=7)
        questions_this_month = get_questions_this_month(s["id"])
        progress = get_student_progress(s["id"])
        log_event(s["id"], "page_view", {"page": "dashboard"})
        return render_template("dashboard.html", s=s, admin_email=config.ADMIN_EMAIL, docs=docs,
                               active="dashboard", max_docs=config.MAX_DOCS_PER_STUDENT,
                               upcoming_deadlines=upcoming_deadlines,
                               questions_this_month=questions_this_month,
                               classifications=config.CLASSIFICATIONS, majors=config.MAJORS,
                               course_colors=course_colors, progress=progress)
    except Exception as e:
        log_error("dashboard.dashboard", e)
        return f"<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><input type='hidden' name='csrf_token' value='{generate_csrf_token()}'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    try:
        s = g.student
        data = request.get_json() or {}
        first_name = (data.get("first_name") or "").strip()[:100]
        last_name = (data.get("last_name") or "").strip()[:100]
        classification = (data.get("classification") or "").strip()
        major = (data.get("major") or "").strip()
        university = (data.get("university") or "").strip()[:200]
        preferred_language = data.get("preferred_language")
        if not all([first_name, last_name, classification, major, university]):
            return jsonify({"error": "All fields are required."}), 400
        if classification not in config.CLASSIFICATIONS or major not in config.MAJORS:
            return jsonify({"error": "Please choose a valid classification and major."}), 400
        if university not in config.UNIVERSITIES:
            return jsonify({"error": "Please choose your university from the list."}), 400
        if preferred_language is not None and preferred_language and preferred_language not in config.PREFERRED_LANGUAGES:
            return jsonify({"error": "Please choose a supported language."}), 400
        if not config.DB_URL:
            return jsonify({"error": "No database configured."}), 500
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT university FROM students WHERE id=%s", (s["id"],))
        old_university = (cur.fetchone() or {}).get("university") or ""
        if preferred_language is not None:
            cur.execute("""UPDATE students SET first_name=%s, last_name=%s,
                           classification=%s, major=%s, university=%s, preferred_language=%s
                           WHERE id=%s""",
                        (first_name, last_name, classification, major, university, preferred_language, s["id"]))
        else:
            cur.execute("""UPDATE students SET first_name=%s, last_name=%s,
                           classification=%s, major=%s, university=%s WHERE id=%s""",
                        (first_name, last_name, classification, major, university, s["id"]))
        if old_university.strip().lower() != university.strip().lower():
            # Remove deadlines that came from the OLD university's global
            # reference documents — they no longer apply now that the
            # student is somewhere else. Deadlines from the student's own
            # uploaded documents, and from any 'ALL universities' global
            # document, are untouched (a global doc's student_id is NULL,
            # so this can never match a personal upload).
            cur.execute("""DELETE FROM deadlines WHERE student_id=%s AND document_id IN (
                           SELECT id FROM documents WHERE student_id IS NULL
                           AND lower(university) != lower(%s) AND lower(university) != 'all')""",
                        (s["id"], university))
        conn.commit(); cur.close()
        log_event(s["id"], "profile_updated", {"classification": classification, "major": major, "university": university})
        profile = {"first_name": first_name, "last_name": last_name,
                   "classification": classification, "major": major,
                   "university": university}
        if preferred_language is not None:
            profile["preferred_language"] = preferred_language
        return jsonify({"success": True, "profile": profile})
    except Exception as e:
        log_error("dashboard.update_profile", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
