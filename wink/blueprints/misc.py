import os
import time
from datetime import date, datetime, timezone

from flask import Blueprint, g, jsonify, render_template

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import page_login_required
from ..services.analytics import log_event

bp = Blueprint("misc", __name__)

_APP_START_TIME = time.time()


def _run_health_checks():
    """Runs each health check independently so one failure doesn't hide the rest."""
    checks = {}

    # Database connectivity
    if config.DB_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            checks["database"] = (True, "Connected")
        except Exception as e:
            log_error("misc.health_db_check", e)
            checks["database"] = (False, "Connection failed")
    else:
        checks["database"] = (False, "DB_URL not configured")

    # Anthropic API key present (does not make a live API call)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(config, "ANTHROPIC_API_KEY", None)
    checks["anthropic_api_key"] = (bool(anthropic_key), "Configured" if anthropic_key else "Missing")

    # Outbound email (SES) configuration
    email_configured = getattr(config, "EMAIL_CONFIGURED", False)
    checks["email_sending"] = (bool(email_configured), "Configured" if email_configured else "Not configured")

    # Admin contact configured (used on /privacy, error pages, etc.)
    admin_email = getattr(config, "ADMIN_EMAIL", None)
    checks["admin_email"] = (bool(admin_email), "Configured" if admin_email else "Missing")

    return checks


@bp.route("/")
def landing():
    try:
        return render_template("landing.html")
    except Exception as e:
        log_error("misc.landing", e)
        return render_template("landing.html")


@bp.route("/privacy")
def privacy():
    # Intentionally public (no login_required) — AWS SES reviewers and
    # prospective users need to read this without an account.
    try:
        return render_template(
            "privacy.html",
            admin_email=config.ADMIN_EMAIL,
            updated_date=date.today().strftime("%B %-d, %Y"),
        )
    except Exception as e:
        log_error("misc.privacy", e)
        return render_template("privacy.html", admin_email=config.ADMIN_EMAIL, updated_date="")


@bp.route("/manual")
@page_login_required
def manual():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "manual"})
        return render_template("manual.html", s=s, admin_email=config.ADMIN_EMAIL, active="manual")
    except Exception as e:
        log_error("misc.manual", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/health")
def health():
    checks = _run_health_checks()
    # Only DB connectivity affects overall status/HTTP code — the rest
    # (API key, email, admin contact) are informational, not outage-causing.
    db_ok = checks["database"][0]
    status = "ok" if db_ok else "degraded"
    return jsonify({
        "status": status,
        "checks": {name: {"ok": ok, "detail": detail} for name, (ok, detail) in checks.items()},
        "uptime_seconds": round(time.time() - _APP_START_TIME),
    }), (200 if db_ok else 503)


@bp.route("/health-page")
def health_page():
    checks = _run_health_checks()
    db_ok = checks["database"][0]
    overall_status = "ok" if db_ok else "degraded"
    uptime_seconds = round(time.time() - _APP_START_TIME)
    try:
        return render_template(
            "health.html",
            overall_status=overall_status,
            checks=checks,
            uptime_seconds=uptime_seconds,
            checked_at=datetime.now(timezone.utc).strftime("%b %-d, %Y %I:%M %p UTC"),
        )
    except Exception as e:
        log_error("misc.health_page", e)
        return jsonify({"status": overall_status}), (200 if db_ok else 503)
