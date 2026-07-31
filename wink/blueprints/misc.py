from flask import Blueprint, jsonify, render_template

from .. import config
from ..extensions import get_db

bp = Blueprint("misc", __name__)


@bp.route("/")
def landing():
    # Always show landing page so students see the welcome screen first
    try:
        return render_template("landing.html")
    except Exception as e:
        print(f"landing error: {e}"); return render_template("landing.html")


@bp.route("/health")
def health():
    db_ok = False
    if config.DB_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            db_ok = True
            cur.close()
        except Exception as e:
            print(f"health check db error: {e}")
    return jsonify({
        "status": "ok" if (db_ok or not config.DB_URL) else "degraded",
        "db": db_ok,
        "api_key": bool(config.ANTHROPIC_API_KEY),
    })
