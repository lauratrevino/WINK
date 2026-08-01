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
    """Render's health check needs to know if this instance is actually
    degraded (so it can restart/route around it) — but the detailed
    per-dependency status (whether the DB specifically is down, whether an
    API key specifically is missing) used to be exposed here too, which
    handed anyone profiling the site a free, unauthenticated signal about
    which internal system was misconfigured. Now only a minimal
    status is public; the real check still runs underneath it."""
    db_ok = True
    if config.DB_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
        except Exception as e:
            print(f"health check db error: {e}")
            db_ok = False
    return jsonify({"status": "ok" if db_ok else "degraded"})
