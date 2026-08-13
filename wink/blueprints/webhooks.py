import json
import logging
import urllib.request

from flask import Blueprint, request

from .. import config
from ..errors import log_error
from ..extensions import csrf
from ..services.ses_notifications import handle_ses_event, verify_sns_signature

bp = Blueprint("webhooks", __name__)
logger = logging.getLogger(__name__)


@bp.route("/webhooks/ses-notifications", methods=["POST"])
@csrf.exempt
def ses_notifications():
    """Amazon SNS delivers SES bounce/complaint/delivery events here. This
    is necessarily a public, unauthenticated-by-login endpoint — AWS calls
    it directly, not a logged-in student's browser — so the actual security
    control is the SNS signature verification below, not a session check."""
    try:
        raw = request.get_data()
        try:
            payload = json.loads(raw)
        except Exception:
            return "", 400

        msg_type = payload.get("Type")

        if config.SES_NOTIFICATION_TOPIC_ARN and payload.get("TopicArn") != config.SES_NOTIFICATION_TOPIC_ARN:
            logger.warning("SES webhook: TopicArn mismatch (got %r)", payload.get("TopicArn"))
            return "", 403

        if not verify_sns_signature(payload):
            return "", 403

        if msg_type == "SubscriptionConfirmation":
            # One-time handshake: SNS won't actually deliver notifications
            # to this endpoint until we visit the URL it gives us here,
            # proving we control it.
            subscribe_url = payload.get("SubscribeURL", "")
            if subscribe_url.startswith("https://sns."):
                try:
                    with urllib.request.urlopen(subscribe_url, timeout=10):
                        pass
                    logger.info("SES webhook: confirmed SNS subscription.")
                except Exception as e:
                    log_error("webhooks.ses_notifications.confirm_subscription", e)
            return "", 200

        if msg_type == "Notification":
            handle_ses_event(payload)
            return "", 200

        # UnsubscribeConfirmation or anything else — acknowledge, do nothing.
        return "", 200
    except Exception as e:
        log_error("webhooks.ses_notifications", e)
        return "", 500
