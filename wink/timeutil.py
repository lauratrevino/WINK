from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_valid_timezone(tz_name):
    """True only for a real IANA zone name (e.g. "America/Denver") — used
    to validate a browser-supplied timezone before it's ever trusted or
    stored, since it arrives as plain user-controlled input."""
    if not tz_name or not isinstance(tz_name, str) or len(tz_name) > 100:
        return False
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def resolve_student_timezone(student):
    """The single place that decides "what timezone is this student in"
    for any date/time math tied to a specific student (deadline
    extraction, spaced-repetition scheduling, activity-chart bucketing,
    reminder emails). `student` is a dict-like row from the students
    table (or None).

    Falls back to config.APP_TIMEZONE (Mountain Time) when the student
    has no timezone on file — which covers three real cases at once: an
    account that selected "Other" as their university with nothing more
    specific to go on, an account created before this column existed,
    and the (should-be-impossible, but checked anyway) case of a stored
    value that isn't actually a valid IANA zone."""
    from . import config  # deferred to avoid a circular import at module load time
    tz = (student or {}).get("timezone") if student else None
    return tz if is_valid_timezone(tz) else config.APP_TIMEZONE
