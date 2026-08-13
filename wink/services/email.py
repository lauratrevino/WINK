import logging

from .. import config

logger = logging.getLogger(__name__)


def send_email(to_email, subject, body):
    if not config.EMAIL_CONFIGURED:
        logger.info("EMAIL (not sent — SMTP not configured) to=%s subject=%r", to_email, subject)
        return False
    to_email = str(to_email).replace("\r", "").replace("\n", "")
    subject = str(subject).replace("\r", "").replace("\n", "")
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
