"""Handles Amazon SES bounce/complaint/delivery notifications, delivered via
Amazon SNS as an HTTP webhook.

Why this exists: AWS's SES "request production access" form specifically
asks how bounces and complaints are handled — sending to an address that
keeps hard-bouncing, or one that has marked WINK's mail as spam, damages the
sending domain's reputation and can get the whole account suspended. This
module makes WINK an active participant: it verifies each notification
really came from AWS (not a forged request), records what happened, and
stops sending to any address that hard-bounces or complains.

Every message SNS sends is itself signed by AWS. We verify that signature
before trusting anything in the payload — an unverified webhook would let
anyone POST fake bounce data and silently stop WINK from emailing an
arbitrary student.
"""
import json
import logging
import re
import time
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from .. import config
from ..errors import log_error
from ..extensions import db_cursor

logger = logging.getLogger(__name__)

# Only ever fetch signing certs from AWS's own SNS domains — this is the
# actual thing that prevents someone from pointing SigningCertURL at a
# certificate they control and having us trust it.
_VALID_CERT_HOST = re.compile(r"^sns\.[a-zA-Z0-9\-]+\.amazonaws\.com$")

_cert_cache = {}
_CERT_CACHE_TTL_SECONDS = 3600


def _fetch_signing_cert(url):
    now = time.time()
    cached = _cert_cache.get(url)
    if cached and now - cached[0] < _CERT_CACHE_TTL_SECONDS:
        return cached[1]
    with urllib.request.urlopen(url, timeout=10) as resp:
        pem_bytes = resp.read()
    cert = x509.load_pem_x509_certificate(pem_bytes)
    _cert_cache[url] = (now, cert)
    return cert


def _canonical_string(payload, fields):
    parts = []
    for field in fields:
        if field not in payload:
            continue
        parts.append(field)
        parts.append(str(payload[field]))
    return ("\n".join(parts) + "\n").encode("utf-8")


# Field order matters — this must match AWS's documented signing order
# exactly, not just include the right fields.
_NOTIFICATION_FIELDS = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
_SUBSCRIPTION_FIELDS = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]


def verify_sns_signature(payload):
    """Returns True only if this payload is authentically signed by AWS SNS.
    Fails closed — any missing field, network error, or verification
    failure returns False rather than assuming the message is legitimate."""
    try:
        cert_url = payload.get("SigningCertURL", "")
        parsed_host = re.match(r"^https://([^/]+)/", cert_url + "/")
        if not parsed_host or not _VALID_CERT_HOST.match(parsed_host.group(1)):
            logger.warning("SNS verification failed: SigningCertURL host not a valid AWS SNS domain: %r", cert_url)
            return False

        msg_type = payload.get("Type")
        if msg_type in ("SubscriptionConfirmation", "UnsubscribeConfirmation"):
            fields = _SUBSCRIPTION_FIELDS
        else:
            fields = _NOTIFICATION_FIELDS
        to_sign = _canonical_string(payload, fields)

        sig_version = str(payload.get("SignatureVersion", "1"))
        algo = hashes.SHA256() if sig_version == "2" else hashes.SHA1()

        import base64
        signature = base64.b64decode(payload.get("Signature", ""))

        cert = _fetch_signing_cert(cert_url)
        public_key = cert.public_key()
        public_key.verify(signature, to_sign, padding.PKCS1v15(), algo)
        return True
    except Exception as e:
        logger.warning("SNS signature verification failed: %s", e)
        return False


def log_email_event(email, event_type, detail, message_id=None):
    if not config.DB_URL:
        return
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO email_events(email, event_type, detail, raw_message_id) VALUES(%s,%s,%s,%s)",
                (email, event_type, detail, message_id),
            )
    except Exception as e:
        log_error("services.ses_notifications.log_email_event", e)


def suppress_email(email, reason):
    if not config.DB_URL:
        return
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO email_suppressions(email, reason) VALUES(%s,%s) ON CONFLICT (email) DO NOTHING",
                (email, reason),
            )
        logger.info("Suppressed future email to %s (reason: %s)", email, reason)
    except Exception as e:
        log_error("services.ses_notifications.suppress_email", e)


def is_suppressed(email):
    if not config.DB_URL or not email:
        return False
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1 FROM email_suppressions WHERE email=%s", (email.strip().lower(),))
            return cur.fetchone() is not None
    except Exception as e:
        log_error("services.ses_notifications.is_suppressed", e)
        return False


def handle_ses_event(payload):
    """payload is the outer SNS envelope; payload['Message'] is a JSON
    string containing the actual SES event (bounce/complaint/delivery)."""
    try:
        message = json.loads(payload.get("Message", "{}"))
    except Exception as e:
        log_error("services.ses_notifications.handle_ses_event.parse", e)
        return

    notification_type = message.get("notificationType") or message.get("eventType")
    message_id = (message.get("mail") or {}).get("messageId")

    if notification_type == "Bounce":
        bounce = message.get("bounce", {})
        bounce_type = bounce.get("bounceType", "Unknown")  # "Permanent" or "Transient"
        bounce_subtype = bounce.get("bounceSubType", "")
        for recipient in bounce.get("bouncedRecipients", []):
            email = (recipient.get("emailAddress") or "").strip().lower()
            if not email:
                continue
            log_email_event(email, "bounce", f"{bounce_type}/{bounce_subtype}", message_id)
            if bounce_type == "Permanent":
                # A permanent bounce means the address doesn't exist / can
                # never receive mail — retrying only hurts sender
                # reputation. A transient bounce (mailbox full, temporary
                # server issue) is left alone; the address may work later.
                suppress_email(email, "bounce_permanent")

    elif notification_type == "Complaint":
        complaint = message.get("complaint", {})
        feedback_type = complaint.get("complaintFeedbackType", "")
        for recipient in complaint.get("complainedRecipients", []):
            email = (recipient.get("emailAddress") or "").strip().lower()
            if not email:
                continue
            log_email_event(email, "complaint", feedback_type, message_id)
            # Any complaint — regardless of type — means the recipient
            # marked this as spam. Never email that address again.
            suppress_email(email, "complaint")

    elif notification_type == "Delivery":
        delivery = message.get("delivery", {})
        for email in delivery.get("recipients", []):
            log_email_event((email or "").strip().lower(), "delivery", None, message_id)

    else:
        logger.info("Unhandled SES notification type: %r", notification_type)
