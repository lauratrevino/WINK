from flask import Blueprint, g, jsonify, render_template

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import page_login_required
from ..services.analytics import log_event

bp = Blueprint("misc", __name__)


@bp.route("/")
def landing():
    try:
        return render_template("landing.html")
    except Exception as e:
        log_error("misc.landing", e)
        return render_template("landing.html")


@bp.route("/manual")
@page_login_required
def manual():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "manual"})
        return render_template("manual.html", s=s, admin_email=config.ADMIN_EMAIL, active="manual")
    except Exception as e:
        log_error("misc.manual", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <a href='/logout'>log out</a> and back in.</p>", 500


@bp.route("/health")
def health():
    db_ok = True
    if config.DB_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
        except Exception as e:
            log_error("misc.health_db_check", e)
            db_ok = False
    status = "ok" if db_ok else "degraded"
    return jsonify({"status": status}), (200 if db_ok else 503)
