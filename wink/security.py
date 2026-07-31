"""
Everything related to "who is this, are they allowed to do this, and are
they doing it too fast": current_student(), the login/admin decorators, and
rate limiting. Centralizing these in one module (instead of copy-pasted
inline checks at the top of every route) means the check only needs to be
correct in one place.
"""
import random
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import g, jsonify, redirect, session, url_for

from . import config
from .extensions import get_db


# ── Current student ───────────────────────────────────────────
def current_student():
    if "sid" not in session or not config.DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM students WHERE id=%s", (session["sid"],))
        s = cur.fetchone(); cur.close()
        if s and not s.get("is_active", True):
            session.clear()
            return None
        return dict(s) if s else None
    except Exception as e:
        print(f"current_student error: {e}"); return None


# ── Auth decorators ───────────────────────────────────────────
def login_required(f):
    """Centralizes the 'is someone logged in' check. Puts the student on
    `g.student` so the view can use it without a second lookup, and keeps
    the check impossible to forget on a new route."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        s = current_student()
        if not s:
            return jsonify({"error": "Not logged in"}), 401
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def page_login_required(f):
    """Like login_required, but for full-page (non-JSON) routes: redirects
    to the login page instead of returning a 401 JSON body."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        s = current_student()
        if not s:
            return redirect(url_for("auth.login"))
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Same as login_required, but also enforces the admin-only check. One
    place to get the check right instead of many separate copies."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        s = current_student()
        if not s:
            return jsonify({"error": "Not logged in"}), 401
        if s["email"].lower() != config.ADMIN_EMAIL:
            return jsonify({"error": "Not authorized"}), 403
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def admin_page_required(f):
    """Like admin_required, but for full-page (non-JSON) routes: redirects
    instead of returning a 401/403 JSON body."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        s = current_student()
        if not s:
            return redirect(url_for("auth.login"))
        if s["email"].lower() != config.ADMIN_EMAIL:
            return redirect(url_for("dashboard.dashboard"))
        g.student = s
        return f(*args, **kwargs)
    return wrapper


# ── Rate limiting ─────────────────────────────────────────────
# Backed by Postgres when a database is configured, so the limit is shared
# and durable across every gunicorn worker, every instance, and every
# restart — the same student/IP can't get extra attempts just by landing on
# a different worker. Falls back to a best-effort, per-process in-memory
# limiter when there's no DATABASE_URL (e.g. running locally without a DB).
_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)


def _rate_limited_memory(key, max_calls, window_seconds):
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= max_calls:
            return round(window_seconds - (now - bucket[0]), 1)
        bucket.append(now)
        return 0


def _rate_limited_db(key, max_calls, window_seconds):
    """Sliding-window limiter stored in the `rate_limits` table. Uses the
    database's own clock (NOW()) for all comparisons rather than each
    worker's local clock, so results are consistent no matter which
    process/instance handles the request."""
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "DELETE FROM rate_limits WHERE key=%s AND ts < NOW() - (%s * INTERVAL '1 second')",
        (key, window_seconds)
    )
    cur.execute(
        "SELECT COUNT(*) AS n, MIN(ts) AS oldest, NOW() AS now_ts FROM rate_limits WHERE key=%s",
        (key,)
    )
    row = cur.fetchone()
    if row["n"] >= max_calls:
        conn.commit(); cur.close()
        wait = window_seconds - (row["now_ts"] - row["oldest"]).total_seconds()
        return round(max(wait, 0), 1)
    cur.execute("INSERT INTO rate_limits(key, ts) VALUES (%s, NOW())", (key,))
    # Opportunistic cleanup of stale rows for keys that never come back
    # (e.g. a one-off attacker IP), so the table doesn't grow unbounded
    # without needing a separate cron job.
    if random.random() < 0.01:
        cur.execute("DELETE FROM rate_limits WHERE ts < NOW() - INTERVAL '1 day'")
    conn.commit(); cur.close()
    return 0


def rate_limited(key, max_calls, window_seconds):
    """Returns 0 if the call is allowed, otherwise the number of seconds
    until the oldest call in the window ages out (so the caller can tell a
    client exactly how long to back off, rather than just "try later").
    Every call site does `if rate_limited(...):`, which works unchanged —
    0 is falsy, any positive number of seconds is truthy."""
    if config.DB_URL:
        try:
            return _rate_limited_db(key, max_calls, window_seconds)
        except Exception as e:
            print(f"rate_limited DB error, falling back to in-memory for this call: {e}")
    return _rate_limited_memory(key, max_calls, window_seconds)


# ── File-signature validation ────────────────────────────
def file_signature_valid(file_storage, ext):
    """True if the file's actual leading bytes match what real files of this
    extension look like. Always true for extensions with no fixed signature
    (txt) — there's nothing meaningful to check there."""
    sigs = config.FILE_SIGNATURES.get(ext)
    if not sigs:
        return True
    try:
        head = file_storage.stream.read(16)
        file_storage.stream.seek(0)
    except Exception:
        return False
    return any(head.startswith(sig) for sig in sigs)
