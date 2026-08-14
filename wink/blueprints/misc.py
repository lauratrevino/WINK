import time
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, render_template

from .. import config
from ..errors import log_error
from ..security import admin_page_required, page_login_required
from ..services.analytics import log_event
from ..services.health import run_health_checks, overall_status

bp = Blueprint("misc", __name__)

_APP_START_TIME = time.time()


@bp.route("/")
def landing():
    try:
        return render_template("landing.html")
    except Exception as e:
        log_error("misc.landing", e)
        return render_template("landing.html")


PRIVACY_EFFECTIVE_DATE = "Monday, August 17th, 2026"


@bp.route("/privacy")
def privacy():
    # Intentionally public (no login_required) — AWS SES reviewers and
    # prospective users need to read this without an account.
    try:
        return render_template(
            "privacy.html",
            admin_email=config.ADMIN_EMAIL,
            updated_date=PRIVACY_EFFECTIVE_DATE,
        )
    except Exception as e:
        log_error("misc.privacy", e)
        return render_template("privacy.html", admin_email=config.ADMIN_EMAIL, updated_date=PRIVACY_EFFECTIVE_DATE)


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
    """Deliberately minimal and unauthenticated — this is the endpoint
    uptime monitors and load balancers hit, so it must stay reachable
    without a session. It returns only pass/fail, never configuration
    detail; the full diagnostic breakdown lives at /health-page, which
    requires an admin login."""
    checks = run_health_checks()
    overall = overall_status(checks)
    return jsonify({
        "status": "ok" if overall != "fail" else "degraded",
    }), (200 if overall != "fail" else 503)


@bp.route("/health-page")
@admin_page_required
def health_page():
    checks = run_health_checks()
    overall = overall_status(checks)
    ok_count = sum(1 for status, _ in checks.values() if status == "ok")
    warn_count = sum(1 for status, _ in checks.values() if status == "warn")
    fail_count = sum(1 for status, _ in checks.values() if status == "fail")
    uptime_seconds = round(time.time() - _APP_START_TIME)
    try:
        return render_template(
            "health.html",
            overall_status=overall,
            checks=checks,
            ok_count=ok_count,
            warn_count=warn_count,
            fail_count=fail_count,
            uptime_seconds=uptime_seconds,
            checked_at=datetime.now(timezone.utc).strftime("%b %-d, %Y %I:%M %p UTC"),
        )
    except Exception as e:
        log_error("misc.health_page", e)
        return jsonify({"status": overall}), (200 if overall != "fail" else 503)
