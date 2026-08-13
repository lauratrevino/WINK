import logging

from .. import config

logger = logging.getLogger(__name__)


def send_email(to_email, subject, body):
    if not config.EMAIL_CONFIGURED:
        logger.info("EMAIL (not sent — SMTP not configured) to=%s subject=%r", to_email, subject)
        return False
    to_email = str(to_email).replace("\r", "").replace("\n", "")
    subject = str(subject).replace("\r", "").replace("\n", "")
    # Imported here rather than at module level purely to keep this file's
    # import list minimal for its main job (sending mail) — there's no
    # circular dependency, ses_notifications.py never imports this module.
    from .ses_notifications import is_suppressed
    if is_suppressed(to_email):
        logger.warning("EMAIL (not sent — recipient is suppressed due to a prior hard bounce or complaint) to=%s subject=%r", to_email, subject)
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = config.FROM_EMAIL
        msg["To"] = to_email
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.sendmail(config.FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception:
        logger.error("send_email error to=%s", to_email, exc_info=True)
        return False
