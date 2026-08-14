
from flask import Blueprint, g, jsonify, render_template

from .. import config
from ..errors import log_error
from ..security import login_required, page_login_required
from ..services.analytics import get_wrapped_stats, log_event
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
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/progress-data")
@login_required
def progress_data():
    try:
        s = g.student
        return jsonify(get_progress_summary(s["id"]))
    except Exception as e:
        log_error("progress.progress_data", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/wrapped-page")
@page_login_required
def wrapped_page():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "wrapped"})
        return render_template("wrapped.html", s=s, admin_email=config.ADMIN_EMAIL, active="progress")
    except Exception as e:
        log_error("progress.wrapped_page", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/wrapped-data")
@login_required
def wrapped_data():
    try:
        s = g.student
        stats = get_wrapped_stats(s["id"])
        if stats is None:
            return jsonify({"error": "No database configured."}), 500
        return jsonify(stats)
    except Exception as e:
        log_error("progress.wrapped_data", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
