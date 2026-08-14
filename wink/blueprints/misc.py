import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timezone

from flask import Blueprint, current_app, g, jsonify, render_template

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import page_login_required
from ..services.analytics import log_event

bp = Blueprint("misc", __name__)

_APP_START_TIME = time.time()

# Checks in this set affect the overall status / HTTP code (200 vs 503).
# Everything else is informational only, so e.g. a missing optional API
# key doesn't get reported as a full outage.
_CRITICAL_CHECKS = {"database"}


def _run_health_checks():
    """Runs each check independently (wrapped in try/except) so one
    failing check can't take down the rest of the page."""
    checks = {}

    # --- Database ---------------------------------------------------
    if config.DB_URL:
        try:
            start = time.time()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            ms = round((time.time() - start) * 1000)
            checks["database"] = (True, f"Connected ({ms}ms)")
        except Exception as e:
            log_error("misc.health_db_check", e)
            checks["database"] = (False, "Connection failed")
    else:
        checks["database"] = (False, "DB_URL not configured")

    # --- Anthropic API key (presence only, no live call/spend) ------
    try:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(config, "ANTHROPIC_API_KEY", None)
        checks["anthropic_api_key"] = (bool(anthropic_key), "Configured" if anthropic_key else "Missing")
    except Exception as e:
        log_error("misc.health_anthropic_check", e)
        checks["anthropic_api_key"] = (False, "Check failed")

    # --- Outbound email (SES) ----------------------------------------
    try:
        email_configured = getattr(config, "EMAIL_CONFIGURED", False)
        checks["email_sending"] = (bool(email_configured), "Configured" if email_configured else "Not configured")
    except Exception as e:
        log_error("misc.health_email_check", e)
        checks["email_sending"] = (False, "Check failed")

    # --- Admin contact (used on /privacy, error pages, etc.) ---------
    try:
        admin_email = getattr(config, "ADMIN_EMAIL", None)
        checks["admin_email"] = (bool(admin_email), "Configured" if admin_email else "Missing")
    except Exception as e:
        log_error("misc.health_admin_email_check", e)
        checks["admin_email"] = (False, "Check failed")

    # --- Flask SECRET_KEY (sessions, CSRF signing) --------------------
    try:
        secret_key = current_app.config.get("SECRET_KEY")
        checks["secret_key"] = (bool(secret_key), "Configured" if secret_key else "Missing")
    except Exception as e:
        log_error("misc.health_secret_key_check", e)
        checks["secret_key"] = (False, "Check failed")

    # --- AWS credentials (used for SES + any S3 storage) --------------
    try:
        aws_key = os.environ.get("AWS_ACCESS_KEY_ID") or getattr(config, "AWS_ACCESS_KEY_ID", None)
        checks["aws_credentials"] = (bool(aws_key), "Configured" if aws_key else "Missing")
    except Exception as e:
        log_error("misc.health_aws_check", e)
        checks["aws_credentials"] = (False, "Check failed")

    # --- Upload directory writable ------------------------------------
    try:
        upload_dir = getattr(config, "UPLOAD_FOLDER", None) or tempfile.gettempdir()
        test_path = os.path.join(upload_dir, ".health_check_tmp")
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        checks["upload_storage"] = (True, f"Writable ({upload_dir})")
    except Exception as e:
        log_error("misc.health_storage_check", e)
        checks["upload_storage"] = (False, "Not writable")

    # --- Disk space -----------------------------------------------------
    try:
        total, used, free = shutil.disk_usage("/")
        free_pct = round((free / total) * 100)
        checks["disk_space"] = (free_pct > 10, f"{free_pct}% free")
    except Exception as e:
        log_error("misc.health_disk_check", e)
        checks["disk_space"] = (False, "Check failed")

    # --- Static assets (landing hero image actually on disk) ----------
    try:
        static_folder = current_app.static_folder or ""
        hero_path = os.path.join(static_folder, "images", "landing-hero.jpg")
        exists = os.path.exists(hero_path)
        checks["static_assets"] = (exists, "Found" if exists else "landing-hero.jpg missing")
    except Exception as e:
        log_error("misc.health_static_check", e)
        checks["static_assets"] = (False, "Check failed")

    # --- Environment / runtime info (always informational) ------------
    try:
        env_name = getattr(config, "ENV", None) or os.environ.get("FLASK_ENV") or "unknown"
        checks["environment"] = (True, env_name)
    except Exception as e:
        log_error("misc.health_env_check", e)
        checks["environment"] = (True, "unknown")

    try:
        checks["python_version"] = (True, platform.python_version())
    except Exception as e:
        log_error("misc.health_python_version_check", e)
        checks["python_version"] = (True, "unknown")

    # --- Memory usage (best-effort; skipped if psutil isn't installed) --
    try:
        import psutil
        mem = psutil.virtual_memory()
        checks["memory"] = (mem.percent < 90, f"{mem.percent}% used")
    except ImportError:
        checks["memory"] = (True, "psutil not installed — skipped")
    except Exception as e:
        log_error("misc.health_memory_check", e)
        checks["memory"] = (False, "Check failed")

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
    critical_ok = all(checks[name][0] for name in _CRITICAL_CHECKS if name in checks)
    status = "ok" if critical_ok else "degraded"
    return jsonify({
        "status": status,
        "checks": {name: {"ok": ok, "detail": detail} for name, (ok, detail) in checks.items()},
        "uptime_seconds": round(time.time() - _APP_START_TIME),
    }), (200 if critical_ok else 503)


@bp.route("/health-page")
def health_page():
    checks = _run_health_checks()
    critical_ok = all(checks[name][0] for name in _CRITICAL_CHECKS if name in checks)
    overall_status = "ok" if critical_ok else "degraded"
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
        return jsonify({"status": overall_status}), (200 if critical_ok else 503)
