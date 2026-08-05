
from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import login_required, page_login_required
from ..services.analytics import log_event, get_questions_this_month, get_wrapped_stats
from ..services.deadlines import get_upcoming_deadlines
from ..services.documents import get_docs

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
@page_login_required
def dashboard():
    try:
        s = g.student
        docs = get_docs(s["id"])
        upcoming_deadlines = get_upcoming_deadlines(s["id"], days_ahead=7)
        questions_this_month = get_questions_this_month(s["id"])
        log_event(s["id"], "page_view", {"page": "dashboard"})
        return render_template("dashboard.html", s=s, admin_email=config.ADMIN_EMAIL, docs=docs,
                               active="dashboard", max_docs=config.MAX_DOCS_PER_STUDENT,
                               upcoming_deadlines=upcoming_deadlines,
                               questions_this_month=questions_this_month,
                               preferred_languages=config.PREFERRED_LANGUAGES)
    except Exception as e:
        log_error("dashboard.dashboard", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <a href='/logout'>log out</a> and back in.</p>", 500


@bp.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    """Lets a student edit their own name, classification, and major from the
    dashboard. Email is intentionally not editable here — it's tied to login
    and to the ADMIN_EMAIL check elsewhere, so changing it needs more care
    than a quick profile edit."""
    try:
        s = g.student
        data = request.get_json() or {}
        first_name = (data.get("first_name") or "").strip()[:100]
        last_name = (data.get("last_name") or "").strip()[:100]
        classification = (data.get("classification") or "").strip()
        major = (data.get("major") or "").strip()
        university = (data.get("university") or "").strip()[:200]
        # Optional: not required, and omitting it entirely (older clients,
        # or a future UI that hasn't added the language field yet) leaves
        # it untouched rather than resetting it to auto-detect.
        preferred_language = data.get("preferred_language")
        if not all([first_name, last_name, classification, major, university]):
            return jsonify({"error": "All fields are required."}), 400
        if classification not in config.CLASSIFICATIONS or major not in config.MAJORS:
            return jsonify({"error": "Please choose a valid classification and major."}), 400
        if preferred_language is not None and preferred_language and preferred_language not in config.PREFERRED_LANGUAGES:
            return jsonify({"error": "Please choose a supported language."}), 400
        if not config.DB_URL:
            return jsonify({"error": "No database configured."}), 500
        conn = get_db(); cur = conn.cursor()
        if preferred_language is not None:
            cur.execute("""UPDATE students SET first_name=%s, last_name=%s,
                           classification=%s, major=%s, university=%s, preferred_language=%s
                           WHERE id=%s""",
                        (first_name, last_name, classification, major, university, preferred_language, s["id"]))
        else:
            cur.execute("""UPDATE students SET first_name=%s, last_name=%s,
                           classification=%s, major=%s, university=%s WHERE id=%s""",
                        (first_name, last_name, classification, major, university, s["id"]))
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


@bp.route("/wrapped-page")
@page_login_required
def wrapped_page():
    """WINK Wrapped — a Spotify-Wrapped-style end-of-semester recap, built
    entirely from real activity already being logged for other purposes.
    The one purely-for-fun feature in the app; everything else here aims
    to be useful, this one just aims to be enjoyed."""
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "wrapped"})
        return render_template("wrapped.html", s=s, admin_email=config.ADMIN_EMAIL, active="wrapped")
    except Exception as e:
        log_error("dashboard.wrapped_page", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <a href='/logout'>log out</a> and back in.</p>", 500


@bp.route("/wrapped-data")
@login_required
def wrapped_data():
    s = g.student
    stats = get_wrapped_stats(s["id"])
    return jsonify(stats or {})
