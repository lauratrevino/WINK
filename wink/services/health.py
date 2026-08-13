"""Per-subsystem health checks for the admin-only health page.

Each check_* function returns a dict: {"name", "status", "detail"}.
status is one of "ok", "warn", "down", "not_configured" — the template
maps these to a color/icon. Checks are intentionally lightweight (no
paid API calls) since this page can be loaded repeatedly by an admin
or a monitor; "configured vs not" is checked for paid providers rather
than making a live billed request on every page view.
"""
import os

from .. import config
from ..extensions import anthropic_client, get_db, voyage_client


def check_database():
    if not config.DB_URL:
        return {"name": "Database", "status": "not_configured", "detail": "No DATABASE_URL set."}
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        return {"name": "Database", "status": "ok", "detail": "Connected."}
    except Exception as e:
        return {"name": "Database", "status": "down", "detail": str(e)[:200]}


def check_anthropic():
    if not config.ANTHROPIC_API_KEY:
        return {"name": "Anthropic (Chat AI)", "status": "not_configured", "detail": "No ANTHROPIC_API_KEY set."}
    if anthropic_client is None:
        return {"name": "Anthropic (Chat AI)", "status": "down", "detail": "Key is set but client failed to initialize."}
    return {"name": "Anthropic (Chat AI)", "status": "ok",
            "detail": "Configured. This check doesn't make a live call — "
                      "it won't detect an out-of-credit account until a chat is actually sent."}


def check_voyage():
    if not config.VOYAGE_API_KEY:
        return {"name": "Voyage (semantic search)", "status": "not_configured",
                "detail": "No VOYAGE_API_KEY set — retrieval falls back to TF-IDF."}
    if voyage_client is None:
        return {"name": "Voyage (semantic search)", "status": "warn",
                "detail": "Key is set but the voyageai package isn't installed — falling back to TF-IDF."}
    return {"name": "Voyage (semantic search)", "status": "ok", "detail": "Configured and enabled."}


def check_email():
    if not config.EMAIL_CONFIGURED:
        return {"name": "Email (AWS SES)", "status": "not_configured",
                "detail": "SMTP_HOST/SMTP_USER/SMTP_PASS not fully set — verification and reminder emails won't send."}
    return {"name": "Email (AWS SES)", "status": "ok", "detail": f"Configured via {config.SMTP_HOST}."}


def check_bounce_handling():
    if not config.DB_URL:
        return {"name": "Bounce/complaint handling", "status": "not_configured", "detail": "No DATABASE_URL set."}
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as n FROM email_suppressions")
        suppressed = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM email_events WHERE created_at > NOW() - INTERVAL '7 days'")
        recent_events = cur.fetchone()["n"]
        cur.execute("SELECT MAX(created_at) as t FROM email_events")
        last_event = cur.fetchone()["t"]
        cur.close()
        if last_event is None:
            return {"name": "Bounce/complaint handling", "status": "warn",
                    "detail": "No SES notifications have ever been received — confirm the SNS subscription "
                              "to /webhooks/ses-notifications is set up and confirmed in the AWS console."}
        return {"name": "Bounce/complaint handling", "status": "ok",
                "detail": f"{suppressed} address(es) suppressed total; {recent_events} event(s) in the last 7 days; "
                          f"last event received {last_event}."}
    except Exception as e:
        return {"name": "Bounce/complaint handling", "status": "warn", "detail": f"Couldn't check: {str(e)[:200]}"}


def check_document_parsing():
    missing = []
    for mod in ("pypdf", "docx", "pptx", "openpyxl", "PIL"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return {"name": "Document parsing", "status": "down",
                "detail": f"Missing packages: {', '.join(missing)}."}

    ocr_detail = "OCR (pytesseract) available."
    ocr_status = "ok"
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
    except ImportError:
        ocr_status, ocr_detail = "down", "pytesseract package not installed."
    except Exception:
        # Package installed but the tesseract binary itself isn't on the
        # system — this is the actual common deployment gotcha (pip
        # installing the wrapper doesn't install the underlying binary).
        ocr_status, ocr_detail = "warn", "pytesseract installed, but the tesseract binary isn't found on this system — scanned-image OCR will fail."

    return {"name": "Document parsing", "status": ocr_status,
            "detail": f"PDF/Word/PowerPoint/Excel parsers OK. {ocr_detail}"}


def check_storage():
    try:
        os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
        test_path = os.path.join(config.UPLOAD_FOLDER, ".health_check_tmp")
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return {"name": "Storage (uploads folder)", "status": "ok", "detail": config.UPLOAD_FOLDER}
    except Exception as e:
        return {"name": "Storage (uploads folder)", "status": "down", "detail": str(e)[:200]}


def check_reminders():
    if not getattr(config, "CRON_SECRET", None):
        return {"name": "Scheduled reminders", "status": "not_configured",
                "detail": "No CRON_SECRET set — /send-deadline-reminders is unreachable, so nothing can trigger it."}
    if not config.DB_URL:
        return {"name": "Scheduled reminders", "status": "warn",
                "detail": "Endpoint is configured and protected, but no database is available to check its run history."}
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT started_at, completed_at, number_processed, number_sent,
                       number_failed, last_error FROM cron_runs
                       WHERE job_name='send_deadline_reminders' ORDER BY started_at DESC LIMIT 1""")
        row = cur.fetchone(); cur.close()
        if not row:
            return {"name": "Scheduled reminders", "status": "warn",
                    "detail": "Endpoint is configured and protected, but has never been called yet — "
                              "confirm your external scheduler is set up."}
        if row["last_error"]:
            return {"name": "Scheduled reminders", "status": "down",
                    "detail": f"Last run at {row['started_at']} failed: {row['last_error'][:200]}"}
        if not row["completed_at"]:
            return {"name": "Scheduled reminders", "status": "warn",
                    "detail": f"A run started at {row['started_at']} but never recorded completion — it may have crashed or timed out."}
        return {"name": "Scheduled reminders", "status": "ok",
                "detail": f"Last ran {row['completed_at']} — {row['number_sent']} sent, "
                          f"{row['number_failed']} failed, {row['number_processed']} students processed."}
    except Exception as e:
        return {"name": "Scheduled reminders", "status": "warn", "detail": f"Couldn't check run history: {str(e)[:200]}"}


def check_weekly_digest():
    if not getattr(config, "CRON_SECRET", None):
        return {"name": "Weekly digest", "status": "not_configured",
                "detail": "No CRON_SECRET set — /send-weekly-digest is unreachable, so nothing can trigger it."}
    if not config.DB_URL:
        return {"name": "Weekly digest", "status": "warn",
                "detail": "Endpoint is configured and protected, but no database is available to check its run history."}
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT started_at, completed_at, number_processed, number_sent,
                       number_failed, last_error FROM cron_runs
                       WHERE job_name='send_weekly_digest' ORDER BY started_at DESC LIMIT 1""")
        row = cur.fetchone(); cur.close()
        if not row:
            return {"name": "Weekly digest", "status": "warn",
                    "detail": "Endpoint is configured and protected, but has never been called yet — "
                              "confirm your external scheduler is set up (once a week, e.g. Monday morning)."}
        if row["last_error"]:
            return {"name": "Weekly digest", "status": "down",
                    "detail": f"Last run at {row['started_at']} failed: {row['last_error'][:200]}"}
        if not row["completed_at"]:
            return {"name": "Weekly digest", "status": "warn",
                    "detail": f"A run started at {row['started_at']} but never recorded completion — it may have crashed or timed out."}
        return {"name": "Weekly digest", "status": "ok",
                "detail": f"Last ran {row['completed_at']} — {row['number_sent']} sent, "
                          f"{row['number_failed']} failed, {row['number_processed']} students processed."}
    except Exception as e:
        return {"name": "Weekly digest", "status": "warn", "detail": f"Couldn't check run history: {str(e)[:200]}"}


def check_conversation_purge():
    if not getattr(config, "CRON_SECRET", None):
        return {"name": "Deleted-conversation purge", "status": "not_configured",
                "detail": "No CRON_SECRET set — /purge-deleted-conversations is unreachable, so nothing can trigger it."}
    if not config.DB_URL:
        return {"name": "Deleted-conversation purge", "status": "warn",
                "detail": "Endpoint is configured and protected, but no database is available to check its run history."}
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT started_at, completed_at, number_processed, last_error FROM cron_runs
                       WHERE job_name='purge_deleted_conversations' ORDER BY started_at DESC LIMIT 1""")
        row = cur.fetchone(); cur.close()
        if not row:
            return {"name": "Deleted-conversation purge", "status": "warn",
                    "detail": "Endpoint is configured and protected, but has never been called yet — "
                              "confirm your external scheduler is set up (student-deleted conversations "
                              "are meant to be hard-deleted 3 months after deletion)."}
        if row["last_error"]:
            return {"name": "Deleted-conversation purge", "status": "down",
                    "detail": f"Last run at {row['started_at']} failed: {row['last_error'][:200]}"}
        if not row["completed_at"]:
            return {"name": "Deleted-conversation purge", "status": "warn",
                    "detail": f"A run started at {row['started_at']} but never recorded completion — it may have crashed or timed out."}
        return {"name": "Deleted-conversation purge", "status": "ok",
                "detail": f"Last ran {row['completed_at']} — {row['number_processed']} conversation(s) purged."}
    except Exception as e:
        return {"name": "Deleted-conversation purge", "status": "warn", "detail": f"Couldn't check run history: {str(e)[:200]}"}


def check_chunking():
    if not config.DB_URL:
        return {"name": "Document chunk processing", "status": "not_configured", "detail": "No DATABASE_URL set."}
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as n FROM documents WHERE chunking_failed IS TRUE")
        failed = cur.fetchone()["n"]
        cur.close()
        if failed == 0:
            return {"name": "Document chunk processing", "status": "ok", "detail": "No documents currently flagged with a processing failure."}
        return {"name": "Document chunk processing", "status": "warn",
                "detail": f"{failed} document(s) failed semantic-search chunk processing and won't "
                          f"surface in retrieval-based answers — check My Documents pages or re-upload them."}
    except Exception as e:
        return {"name": "Document chunk processing", "status": "warn", "detail": f"Couldn't check: {str(e)[:200]}"}


def get_health_report():
    checks = [
        check_database(),
        check_anthropic(),
        check_voyage(),
        check_email(),
        check_document_parsing(),
        check_storage(),
        check_reminders(),
        check_conversation_purge(),
        check_weekly_digest(),
        check_bounce_handling(),
        check_chunking(),
    ]
    order = {"down": 0, "warn": 1, "not_configured": 2, "ok": 3}
    checks.sort(key=lambda c: order.get(c["status"], 4))
    overall = "ok"
    if any(c["status"] == "down" for c in checks):
        overall = "down"
    elif any(c["status"] in ("warn", "not_configured") for c in checks):
        overall = "warn"
    return {"overall": overall, "checks": checks}
