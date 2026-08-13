
from flask import Blueprint, g, jsonify, render_template

from .. import config
from ..errors import log_error
from ..security import login_required, page_login_required
from ..services.analytics import log_event
from ..services.progress import get_progress_summary

bp = Blueprint("progress", __name__)


@bp.route("/progress-page")
@bp.route("/progress")
@page_login_required
def progress_page():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "progress"})
        return render_template("progress.html", s=s, admin_email=config.ADMIN_EMAIL, active="progress")
    except Exception as e:
        log_error("progress.progress_page", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <a href='/logout'>log out</a> and back in.</p>", 500


@bp.route("/progress-data")
@login_required
def progress_data():
    try:
        s = g.student
        return jsonify(get_progress_summary(s["id"]))
    except Exception as e:
        log_error("progress.progress_data", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
