import logging

logger = logging.getLogger(__name__)


def log_error(where, exc, **context):
    extra = " ".join(f"{k}={v}" for k, v in context.items() if v is not None)
    suffix = f" ({extra})" if extra else ""
    logger.error("%s: %s%s", where, exc, suffix, exc_info=True)
