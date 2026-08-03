"""Plain-text email sending, used by registration, password reset, and
deadline reminders."""
from .. import config


def send_email(to_email, subject, body):
    """Send a plain-text email. Returns True on success. Falls back to
    logging (never raises) if SMTP isn't configured or sending fails, so a
    flaky email provider never breaks the request that triggered it."""
    if not config.EMAIL_CONFIGURED:
        print(f"EMAIL (not sent — SMTP not configured) to={to_email} subject={subject!r}")
        return False
    # Strip any embedded CR/LF before these go into email headers — without
    # this, a crafted "email" ending in .edu but containing e.g.
    # "\r\nBcc: attacker@evil.com" could inject extra headers into the
    # message (header/BCC injection), since the .edu check alone doesn't
    # reject control characters.
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
    except Exception as e:
        print(f"send_email error to={to_email}: {e}")
        return False
