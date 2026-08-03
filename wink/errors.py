"""
Centralized error logging.

Every route and service function in this app catches broadly and degrades
gracefully rather than letting a request crash — that's a deliberate,
consistent design choice documented throughout services/. But it had one
side effect the engineering audit flagged: each of the ~50 call sites
printed its own separately-worded line (`print(f"upload error: {e}")`,
`print(f"chat error: {e}")`, ...), some followed by `traceback.print_exc()`
and some not, with no shared structure to grep Render's logs by. A DB
outage and a bad model response looked identical from the outside, and
there was no single place to point a log aggregator at later.

log_error() is the one place all of those now funnel through: same
timestamp format, same "[ERROR] <where>: <message>" shape, and the
traceback printed every time (a few call sites used to skip it). Still
print-based — that's exactly what Render's log viewer already captures —
this isn't a new logging service or a new dependency, just consistency.
"""
import traceback
from datetime import datetime, timezone


def log_error(where, exc, **context):
    """where: a short "module.function" or "blueprint.route_name" string
    (e.g. "chat.chat", "services.deadlines.extract_deadlines") so every
    log line is filterable by origin — this replaces the old inconsistent
    "X error: {e}" prefixes with one predictable shape. context: optional
    extra key=value pairs relevant to this specific failure (e.g.
    student_id=s["id"], course=course) — never anything sensitive like a
    password, token, or full document/answer text; those don't belong in
    logs regardless of this helper.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = " ".join(f"{k}={v}" for k, v in context.items() if v is not None)
    suffix = f" ({extra})" if extra else ""
    print(f"[ERROR] {ts} {where}: {exc}{suffix}")
    traceback.print_exc()
