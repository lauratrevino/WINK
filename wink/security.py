import logging
import random
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import g, jsonify, redirect, session, url_for

logger = logging.getLogger(__name__)

from . import config
from .extensions import get_db


def current_student():
    if "sid" not in session or not config.DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM students WHERE id=%s", (session["sid"],))
        s = cur.fetchone(); cur.close()
        if s and s.get("is_active") is False:
            session.clear()
            return None
        if s and s.get("account_deleted_at") is not None:
            session.clear()
            return None
        if s and s.get("is_demo") and s.get("demo_expires_at"):
            from datetime import datetime
            if s["demo_expires_at"] <= datetime.utcnow():
                sid = s["id"]
                cur = conn.cursor()
                cur.execute("DELETE FROM events WHERE student_id=%s", (sid,))
                cur.execute("DELETE FROM students WHERE id=%s AND is_demo=TRUE", (sid,))
                conn.commit(); cur.close()
                session.clear()
                return None
        return dict(s) if s else None
    except Exception:
        logger.error("current_student error", exc_info=True)
        return None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        s = current_student()
        if not s:
            return jsonify({"error": "Not logged in"}), 401
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def page_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        s = current_student()
        if not s:
            return redirect(url_for("auth.login"))
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
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


def verified_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.student.get("email_verified"):
            return jsonify({
                "error": "Please verify your email address first — check your inbox for the "
                         "verification link WINK sent when you registered, or use the resend "
                         "option on your dashboard."
            }), 403
        return f(*args, **kwargs)
    return wrapper


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
    if random.random() < 0.01:
        cur.execute("DELETE FROM rate_limits WHERE ts < NOW() - INTERVAL '1 day'")
    conn.commit(); cur.close()
    return 0


def rate_limited(key, max_calls, window_seconds):
    if config.DB_URL:
        try:
            return _rate_limited_db(key, max_calls, window_seconds)
        except Exception:
            logger.warning("rate_limited DB error, falling back to in-memory for this call", exc_info=True)
    return _rate_limited_memory(key, max_calls, window_seconds)


def file_signature_valid(file_storage, ext):
    sigs = config.FILE_SIGNATURES.get(ext)
    if not sigs:
        return True
    try:
        head = file_storage.stream.read(16)
        file_storage.stream.seek(0)
    except Exception:
        return False
    return any(head.startswith(sig) for sig in sigs)
