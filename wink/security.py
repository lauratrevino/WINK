import logging
import random
import threading
import time
from collections import defaultdict, deque
from functools import wraps

import psycopg2
from psycopg2 import pool as _pg_pool
from flask import g, jsonify, redirect, session, url_for

logger = logging.getLogger(__name__)

from . import config
from .extensions import get_db, db_cursor


class AuthCheckUnavailable(Exception):
    """Raised by current_student() when login status genuinely couldn't be
    determined because of a transient infrastructure problem (the DB
    connection pool momentarily exhausted, a dropped connection, etc) —
    NOT because the session is actually invalid. Callers must not treat
    this the same as "not logged in": doing so previously meant a
    legitimately authenticated request got a 401 purely because of brief
    pool contention under load, telling a real, logged-in student they
    weren't logged in when the truth is just "we couldn't check yet."
    """
    pass


def current_student():
    if "sid" not in session or not config.DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
    except (_pg_pool.PoolError, psycopg2.OperationalError) as e:
        raise AuthCheckUnavailable("database temporarily unavailable") from e
    try:
        cur.execute("SELECT * FROM students WHERE id=%s", (session["sid"],))
        s = cur.fetchone(); cur.close()
        if s and s.get("is_active") is False:
            session.clear()
            return None
        if s and s.get("account_deleted_at") is not None:
            session.clear()
            return None
        if s:
            # A session issued before the account's most recent password
            # change is stale — without this check, resetting a password
            # never actually revokes any session that was already logged
            # in with the old one; it just sits there valid until it
            # expires on its own. pw_changed_at is set into the session at
            # login (both routes below) as the comparison anchor.
            db_pw_changed = s.get("password_changed_at")
            db_pw_changed_str = db_pw_changed.isoformat() if db_pw_changed else None
            if session.get("pw_changed_at") != db_pw_changed_str:
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
    except (psycopg2.OperationalError, psycopg2.InterfaceError, _pg_pool.PoolError) as e:
        # Same reasoning as the connection-acquisition failure above: a DB
        # error here means login status genuinely couldn't be determined,
        # not that the student is actually logged out. Without this, a
        # transient DB blip mid-query (dropped connection, statement
        # timeout, etc.) would silently log out a real, authenticated
        # student instead of surfacing as "try again."
        raise AuthCheckUnavailable("database temporarily unavailable") from e
    except Exception:
        logger.error("current_student error", exc_info=True)
        return None


def _auth_unavailable_json():
    return jsonify({"error": "We're experiencing heavy load right now — please try again in a moment."}), 503


def _auth_unavailable_page():
    return ("<h2>Please try again</h2><p>We're experiencing heavy load right now — "
            "please wait a moment and reload the page.</p>", 503)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            s = current_student()
        except AuthCheckUnavailable:
            return _auth_unavailable_json()
        if not s:
            return jsonify({"error": "Not logged in"}), 401
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def page_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            s = current_student()
        except AuthCheckUnavailable:
            return _auth_unavailable_page()
        if not s:
            return redirect(url_for("auth.login"))
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            s = current_student()
        except AuthCheckUnavailable:
            return _auth_unavailable_json()
        if not s:
            return jsonify({"error": "Not logged in"}), 401
        if s["email"].lower() != config.ADMIN_EMAIL:
            return jsonify({"error": "Not authorized"}), 403
        if s.get("mfa_enabled") and not session.get("mfa_verified"):
            return jsonify({"error": "Two-factor verification required", "mfa_required": True}), 403
        g.student = s
        return f(*args, **kwargs)
    return wrapper


def admin_page_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            s = current_student()
        except AuthCheckUnavailable:
            return _auth_unavailable_page()
        if not s:
            return redirect(url_for("auth.login"))
        if s["email"].lower() != config.ADMIN_EMAIL:
            return redirect(url_for("dashboard.dashboard"))
        if s.get("mfa_enabled") and not session.get("mfa_verified"):
            return redirect(url_for("auth.mfa_verify_page"))
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
    with db_cursor(commit=True) as cur:
        # Without this, two concurrent requests for the same key can both run
        # the COUNT(*) below before either has inserted+committed, both see
        # "under the limit," and both proceed — silently letting a burst exceed
        # max_calls. pg_advisory_xact_lock serializes callers sharing this key
        # (hashed to a lock id) for the rest of this transaction; it's released
        # automatically on commit/rollback, so it never needs manual cleanup and
        # never blocks callers using a *different* key.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
        cur.execute(
            "DELETE FROM rate_limits WHERE key=%s AND ts < NOW() - (%s * INTERVAL '1 second')",
            (key, window_seconds)
        )
        cur.execute(
            "SELECT COUNT(*) AS n, EXTRACT(EPOCH FROM (NOW() - MIN(ts))) AS elapsed_seconds FROM rate_limits WHERE key=%s",
            (key,)
        )
        row = cur.fetchone()
        if row["n"] >= max_calls:
            elapsed = row["elapsed_seconds"] or 0
            return round(max(window_seconds - elapsed, 0), 1)
        cur.execute("INSERT INTO rate_limits(key, ts) VALUES (%s, NOW())", (key,))
        if random.random() < 0.01:
            cur.execute("DELETE FROM rate_limits WHERE ts < NOW() - INTERVAL '1 day'")
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
