"""Canonical health-check logic for the admin-only health page and the
public /health uptime endpoint.

This used to be two separate, disconnected implementations (one inline
in blueprints/misc.py, one here) that silently diverged and collided on
the same URL. They've been merged into this single source of truth —
every check lives here; misc.py and admin.py both call run_health_checks()
and just handle their own HTTP-layer concerns (auth, rendering, JSON
shape) on top of it.

Each check returns a (status, detail) tuple where status is 'ok', 'warn',
or 'fail'. Checks are intentionally lightweight (no paid API calls) since
this page can be loaded repeatedly by an admin or an external monitor.
"""
import os
import platform
import shutil
import tempfile
import time

from flask import current_app

from .. import config
from ..errors import log_error
from ..extensions import get_db

_APP_START_TIME = time.time()

# Checks in this set can flip the overall status to "fail" (HTTP 503 on
# the public /health endpoint). Everything else can still show a warning
# on the admin page, but won't take the public uptime check down.
CRITICAL_CHECKS = {"database"}


def _check_cron_job(job_name, label):
    """Shared logic for the three named scheduled jobs below — each reports
    its own most recent run separately, rather than folding them into one
    generic 'last cron run' check that could hide a job silently going
    stale simply because a different job happened to run more recently."""
    if not getattr(config, "CRON_SECRET", None):
        return ("warn", f"No CRON_SECRET set — {job_name} is unreachable, so nothing can trigger it.")
    if not config.DB_URL:
        return ("warn", "Endpoint is configured, but no database is available to check its run history.")
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT started_at, completed_at, number_processed, number_sent,
                       number_failed, last_error FROM cron_runs
                       WHERE job_name=%s ORDER BY started_at DESC LIMIT 1""", (job_name,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return ("warn", "Configured, but has never been called yet — confirm your external scheduler is set up.")
        if row["last_error"]:
            return ("fail", f"Last run at {row['started_at']} failed: {str(row['last_error'])[:150]}")
        if not row["completed_at"]:
            return ("warn", f"A run started at {row['started_at']} but never recorded completion — it may have crashed or timed out.")
        detail = f"Last ran {row['completed_at']}"
        if row.get("number_sent") is not None:
            detail += f" — {row['number_sent']} sent, {row['number_failed']} failed, {row['number_processed']} processed"
        else:
            detail += f" — {row['number_processed']} processed"
        return ("ok", detail)
    except Exception as e:
        log_error(f"services.health.cron_check.{job_name}", e)
        return ("warn", f"Couldn't check run history: {str(e)[:150]}")


def run_health_checks():
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
            status = "ok" if ms < 500 else "warn"
            checks["database"] = (status, f"Connected ({ms}ms)")
        except Exception as e:
            log_error("services.health.database", e)
            checks["database"] = ("fail", "Connection failed")
    else:
        checks["database"] = ("fail", "DB_URL not configured")

    # --- Anthropic API key (presence only, no live call/spend) ------
    try:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(config, "ANTHROPIC_API_KEY", None)
        checks["anthropic_api_key"] = ("ok" if anthropic_key else "fail", "Configured" if anthropic_key else "Missing")
    except Exception as e:
        log_error("services.health.anthropic", e)
        checks["anthropic_api_key"] = ("fail", "Check failed")

    # --- Voyage API key (neural embeddings vs. TF-IDF retrieval fallback) --
    try:
        voyage_key = os.environ.get("VOYAGE_API_KEY") or getattr(config, "VOYAGE_API_KEY", None)
        checks["voyage_api_key"] = (
            "ok" if voyage_key else "warn",
            "Configured (neural embeddings)" if voyage_key else "Missing — chat falls back to TF-IDF retrieval",
        )
    except Exception as e:
        log_error("services.health.voyage", e)
        checks["voyage_api_key"] = ("fail", "Check failed")

    # --- Outbound email (SES) ----------------------------------------
    try:
        email_configured = getattr(config, "EMAIL_CONFIGURED", False)
        checks["email_sending"] = ("ok" if email_configured else "warn", "Configured" if email_configured else "Not configured")
    except Exception as e:
        log_error("services.health.email", e)
        checks["email_sending"] = ("fail", "Check failed")

    # --- Admin contact (used on /privacy, error pages, etc.) ---------
    try:
        admin_email = getattr(config, "ADMIN_EMAIL", None)
        checks["admin_email"] = ("ok" if admin_email else "warn", "Configured" if admin_email else "Missing")
    except Exception as e:
        log_error("services.health.admin_email", e)
        checks["admin_email"] = ("fail", "Check failed")

    # --- Flask SECRET_KEY (sessions, CSRF signing) --------------------
    try:
        secret_key = current_app.config.get("SECRET_KEY")
        checks["secret_key"] = ("ok" if secret_key else "fail", "Configured" if secret_key else "Missing")
    except Exception as e:
        log_error("services.health.secret_key", e)
        checks["secret_key"] = ("fail", "Check failed")

    # --- AWS credentials (used for SES + any S3 storage) --------------
    try:
        aws_key = os.environ.get("AWS_ACCESS_KEY_ID") or getattr(config, "AWS_ACCESS_KEY_ID", None)
        checks["aws_credentials"] = ("ok" if aws_key else "warn", "Configured" if aws_key else "Missing")
    except Exception as e:
        log_error("services.health.aws", e)
        checks["aws_credentials"] = ("fail", "Check failed")

    # --- Cron secret (authorizes scheduled jobs) -----------------------
    try:
        cron_secret = getattr(config, "CRON_SECRET", None)
        checks["cron_secret"] = (
            "ok" if cron_secret else "warn",
            "Configured" if cron_secret else "Missing — scheduled jobs will reject every call",
        )
    except Exception as e:
        log_error("services.health.cron_secret", e)
        checks["cron_secret"] = ("fail", "Check failed")

    # --- SES notification topic ARN (bounce/complaint webhook) -------------
    try:
        topic_arn = getattr(config, "SES_NOTIFICATION_TOPIC_ARN", None)
        checks["ses_notification_topic"] = ("ok" if topic_arn else "warn", "Configured" if topic_arn else "Missing")
    except Exception as e:
        log_error("services.health.ses_topic", e)
        checks["ses_notification_topic"] = ("fail", "Check failed")

    # --- Bounce/complaint handling (suppression list + recent activity) ---
    try:
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM email_suppressions")
            suppressed = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM email_events WHERE created_at > NOW() - INTERVAL '7 days'")
            recent = cur.fetchone()["n"]
            cur.execute("SELECT MAX(created_at) as t FROM email_events")
            last_event = cur.fetchone()["t"]
            cur.close()
            if last_event is None:
                checks["bounce_handling"] = ("warn", "No SES notifications ever received — confirm the SNS subscription is set up.")
            else:
                checks["bounce_handling"] = ("ok", f"{suppressed} suppressed total, {recent} event(s) in the last 7 days")
        else:
            checks["bounce_handling"] = ("warn", "DB not configured")
    except Exception as e:
        log_error("services.health.bounce_handling", e)
        checks["bounce_handling"] = ("warn", f"Couldn't check: {str(e)[:150]}")

    # --- Scheduled jobs (each reported separately — see _check_cron_job) --
    checks["reminders_cron"] = _check_cron_job("send_deadline_reminders", "Deadline reminders")
    checks["weekly_digest_cron"] = _check_cron_job("send_weekly_digest", "Weekly digest")
    checks["purge_cron"] = _check_cron_job("purge_deleted_conversations", "Conversation purge")

    # --- Document parsing libraries + OCR ------------------------------
    try:
        missing = []
        for mod in ("pypdf", "docx", "pptx", "openpyxl", "PIL"):
            try:
                __import__(mod)
            except ImportError:
                missing.append(mod)
        if missing:
            checks["document_parsing"] = ("fail", f"Missing packages: {', '.join(missing)}")
        else:
            try:
                import pytesseract
                pytesseract.get_tesseract_version()
                checks["document_parsing"] = ("ok", "PDF/Word/PowerPoint/Excel parsers OK. OCR (pytesseract) available.")
            except ImportError:
                checks["document_parsing"] = ("warn", "Document parsers OK, but pytesseract isn't installed — image OCR will fail.")
            except Exception:
                checks["document_parsing"] = ("warn", "Document parsers OK, but the tesseract binary isn't found on this system — image OCR will fail.")
    except Exception as e:
        log_error("services.health.document_parsing", e)
        checks["document_parsing"] = ("fail", "Check failed")

    # --- Document chunk processing failures ----------------------------
    try:
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM documents WHERE chunking_failed IS TRUE")
            failed = cur.fetchone()["n"]
            cur.close()
            if failed == 0:
                checks["chunking"] = ("ok", "No documents currently flagged with a processing failure.")
            else:
                checks["chunking"] = ("warn", f"{failed} document(s) failed semantic-search chunk processing.")
        else:
            checks["chunking"] = ("warn", "DB not configured")
    except Exception as e:
        log_error("services.health.chunking", e)
        checks["chunking"] = ("warn", f"Couldn't check: {str(e)[:150]}")

    # --- Upload directory writable ------------------------------------
    try:
        upload_dir = getattr(config, "UPLOAD_FOLDER", None) or tempfile.gettempdir()
        test_path = os.path.join(upload_dir, ".health_check_tmp")
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        checks["upload_storage"] = ("ok", f"Writable ({upload_dir})")
    except Exception as e:
        log_error("services.health.storage", e)
        checks["upload_storage"] = ("fail", "Not writable")

    # --- Disk space -----------------------------------------------------
    try:
        total, used, free = shutil.disk_usage("/")
        free_pct = round((free / total) * 100)
        if free_pct > 20:
            status = "ok"
        elif free_pct > 10:
            status = "warn"
        else:
            status = "fail"
        checks["disk_space"] = (status, f"{free_pct}% free")
    except Exception as e:
        log_error("services.health.disk", e)
        checks["disk_space"] = ("fail", "Check failed")

    # --- Static assets (landing hero image actually on disk) ----------
    try:
        static_folder = current_app.static_folder or ""
        hero_path = os.path.join(static_folder, "images", "landing-hero.jpg")
        exists = os.path.exists(hero_path)
        checks["static_assets"] = ("ok" if exists else "warn", "Found" if exists else "landing-hero.jpg missing")
    except Exception as e:
        log_error("services.health.static", e)
        checks["static_assets"] = ("fail", "Check failed")

    # --- Environment / runtime info (always informational) ------------
    try:
        env_name = getattr(config, "ENV", None) or os.environ.get("FLASK_ENV") or "unknown"
        checks["environment"] = ("ok", env_name)
    except Exception as e:
        log_error("services.health.environment", e)
        checks["environment"] = ("ok", "unknown")

    try:
        checks["python_version"] = ("ok", platform.python_version())
    except Exception as e:
        log_error("services.health.python_version", e)
        checks["python_version"] = ("ok", "unknown")

    # --- Memory usage (best-effort; skipped if psutil isn't installed) --
    try:
        import psutil
        mem = psutil.virtual_memory()
        if mem.percent < 70:
            status = "ok"
        elif mem.percent < 90:
            status = "warn"
        else:
            status = "fail"
        checks["memory"] = (status, f"{mem.percent}% used")
    except ImportError:
        checks["memory"] = ("ok", "psutil not installed — skipped")
    except Exception as e:
        log_error("services.health.memory", e)
        checks["memory"] = ("fail", "Check failed")

    return checks


def overall_status(checks):
    """'fail' if any critical check failed, else 'warn' if anything is
    amber, else 'ok'."""
    if any(checks[name][0] == "fail" for name in CRITICAL_CHECKS if name in checks):
        return "fail"
    if any(status == "warn" for status, _ in checks.values()):
        return "warn"
    if any(status == "fail" for status, _ in checks.values()):
        return "warn"  # a non-critical check failed — flag it, but don't 503
    return "ok"
