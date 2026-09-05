from datetime import datetime, timedelta, timezone
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


def relative_day_label(target_date, today_date):
    """Deterministic 'today' / 'tomorrow' / 'in N days' / 'N days ago'
    label for target_date relative to today_date — both plain date
    objects. Exists so callers never ask the AI model to work this out
    itself: a live pilot test showed the model mislabeling a correct,
    database-sourced due date (calling a Monday "Sunday" and calling a
    date two days out "tomorrow") even though the correct date and
    weekday were already given to it in the prompt. Pure date-diff
    arithmetic in application code cannot make that mistake."""
    delta = (target_date - today_date).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if delta > 1:
        return f"in {delta} days"
    return f"{abs(delta)} days ago"


def build_date_reference_block(now):
    """A deterministic weekday/relative-date lookup table for the next 14
    days, computed here in application code rather than left for the AI
    model to compute at answer time. `now` is an aware datetime already
    resolved to the student's local timezone (see
    resolve_student_timezone). Meant to be included verbatim in the chat
    system prompt so the model can look up any weekday or relative-date
    label instead of calculating one — see relative_day_label's docstring
    for why that calculation is not safe to leave to the model."""
    today = now.date()
    lines = [
        "DATE REFERENCE (already computed — read a weekday or relative-day "
        "label from this table instead of calculating one yourself; this "
        "table is guaranteed correct, your own date arithmetic is not):",
    ]
    for i in range(14):
        d = today + timedelta(days=i)
        label = relative_day_label(d, today)
        lines.append(f"- {d.strftime('%Y-%m-%d')} = {d.strftime('%A')} ({label})")
    return "\n".join(lines)
