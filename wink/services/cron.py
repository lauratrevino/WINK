import functools
import secrets

from flask import jsonify, request

from .. import config
from ..errors import log_error
from ..extensions import db_cursor


def _provided_secret():
    provided = request.headers.get("X-WINK-Cron-Secret", "")
    if not provided:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[len("Bearer "):]
    return provided


def is_authorized_cron_request():
    """True only if the request carries a secret that matches
    config.CRON_SECRET. Used directly by anything that needs the check
    without the full cron_job() wrapper (e.g. health.py's status page)."""
    provided = _provided_secret()
    return bool(config.CRON_SECRET) and secrets.compare_digest(provided, config.CRON_SECRET)


def cron_job(job_name, skip_check=None):
    """Decorator for a scheduled-job route. Handles the auth check, the
    cron_runs bookkeeping (insert on start, update with stats on success,
    record last_error on failure), and turns any exception the wrapped
    view raises into a logged failure with a 500 response — previously
    every one of these four endpoints (send_deadline_reminders,
    send_weekly_digest, purge_deleted_conversations,
    purge_expired_demos) reimplemented this same block by hand.

    The wrapped view receives `run_id` as a keyword argument and should
    return a dict. Recognized keys `number_processed`, `number_sent`, and
    `number_failed` are written to cron_runs; everything else in the dict
    (or the whole dict, if none of those keys are present) is returned to
    the caller as the JSON response body.

    `skip_check`, if given, is a zero-argument callable checked BEFORE a
    cron_runs row is created for this invocation. Returning a non-empty
    string skips the job entirely (no run row inserted) and responds with
    {"skipped": True, "reason": <that string>} — used by
    send_weekly_digest to avoid double-sending within the same week
    without polluting cron_runs with a run row for a job that never
    actually ran.
    """
    STAT_KEYS = ("number_processed", "number_sent", "number_failed")

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_authorized_cron_request():
                return jsonify({"error": "Not authorized"}), 403
            if not config.DB_URL:
                return jsonify({"error": "No database"}), 500

            if skip_check is not None:
                reason = skip_check()
                if reason:
                    return jsonify({"skipped": True, "reason": reason})

            with db_cursor(commit=True) as cur:
                cur.execute("INSERT INTO cron_runs(job_name) VALUES(%s) RETURNING id", (job_name,))
                run_id = cur.fetchone()["id"]

            try:
                result = fn(*args, run_id=run_id, **kwargs) or {}
                with db_cursor(commit=True) as cur:
                    cur.execute(
                        """UPDATE cron_runs SET completed_at=NOW(), number_processed=%s,
                           number_sent=%s, number_failed=%s WHERE id=%s""",
                        (result.get("number_processed", 0), result.get("number_sent", 0),
                         result.get("number_failed", 0), run_id),
                    )
                response_body = {k: v for k, v in result.items() if k not in STAT_KEYS}
                return jsonify(response_body or result)
            except Exception as e:
                log_error(f"cron.{job_name}", e)
                try:
                    with db_cursor(commit=True) as cur:
                        cur.execute("UPDATE cron_runs SET completed_at=NOW(), last_error=%s WHERE id=%s",
                                    (str(e)[:500], run_id))
                except Exception:
                    pass
                return jsonify({"error": "Something went wrong on our end."}), 500
        return wrapper
    return decorator
