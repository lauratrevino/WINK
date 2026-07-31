import secrets
import traceback
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import config
from ..extensions import get_db
from ..security import login_required, rate_limited
from ..services.analytics import log_event
from ..services.email import send_email
from ..timeutil import utcnow_naive

bp = Blueprint("auth", __name__)

# Used by login() below so a failed attempt against a nonexistent email
# takes about as long as one against a real email with the wrong password —
# without this, check_password_hash() (deliberately slow) only runs when the
# account exists, making "how fast did it fail" a timing side-channel an
# attacker could use to find out which emails are registered.
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_hex(32))


@bp.route("/register", methods=["GET", "POST"])
def register():
    def err(msg):
        return render_template("register.html", error=msg,
                               classifications=config.CLASSIFICATIONS, majors=config.MAJORS)
    try:
        if request.method == "POST":
            if rate_limited(f"register:{request.remote_addr}", max_calls=8, window_seconds=300):
                return err("Too many attempts — please wait a few minutes and try again.")
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "").strip()
            fn = request.form.get("first_name", "").strip()[:100]
            ln = request.form.get("last_name", "").strip()[:100]
            cl = request.form.get("classification", "").strip()
            major = request.form.get("major", "").strip()
            university = request.form.get("university", "").strip()[:200]
            if not all([email, pw, fn, ln, cl, major, university]):
                return err("All fields are required, including your university.")
            # classification/major are meant to come from the fixed dropdown
            # lists below — someone posting directly to this endpoint (rather
            # than through the form) could otherwise submit any arbitrary
            # string here.
            if cl not in config.CLASSIFICATIONS or major not in config.MAJORS:
                return err("Please choose a valid classification and major.")
            # WINK serves students at any school, not just UTEP — a plain
            # .edu check is a reasonable, low-friction sanity check.
            # EMAIL_RE also rejects whitespace/control characters anywhere in
            # the address — without this, a string ending in ".edu" could
            # still contain an embedded "\r\nBcc: ..." and pass a bare
            # endswith() check, opening an email-header-injection path.
            if not config.EMAIL_RE.match(email):
                return err("Please use your school (.edu) email address.")
            if len(pw) < 8:
                return err("Password must be at least 8 characters.")
            if not config.DB_URL:
                return err("Database not configured.")
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", (email,))
            if cur.fetchone():
                cur.close()
                return err("Account already exists — please log in.")
            cur.execute("""INSERT INTO students(email,password_hash,first_name,last_name,classification,major,university)
                           VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (email, generate_password_hash(pw), fn, ln, cl, major, university))
            new_id = cur.fetchone()["id"]
            verify_token = secrets.token_urlsafe(32)
            cur.execute("UPDATE students SET verification_token=%s WHERE id=%s", (verify_token, new_id))
            conn.commit(); cur.close()
            session.permanent = True  # actually use the 7-day PERMANENT_SESSION_LIFETIME set in create_app
            session["sid"] = new_id
            log_event(new_id, "account_created", {"email": email, "classification": cl, "major": major, "university": university})
            verify_link = url_for("auth.verify_email", token=verify_token, _external=True)
            send_email(email, "Verify your WINK email address",
                       f"Hi {fn},\n\nWelcome to WINK! Please confirm your email address by visiting:\n{verify_link}\n\n"
                       f"You can use WINK right away either way — this just confirms we can reach you.\n\n— WINK")
            return redirect(url_for("documents.documents_page"))
        return render_template("register.html", error=None,
                               classifications=config.CLASSIFICATIONS, majors=config.MAJORS)
    except Exception as e:
        print(f"register error: {e}"); traceback.print_exc()
        return err("Something went wrong on our end. Please try again in a moment.")


@bp.route("/login", methods=["GET", "POST"])
def login():
    try:
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "").strip()
            if rate_limited(f"login:{request.remote_addr}", max_calls=10, window_seconds=60):
                return render_template("login.html", error="Too many attempts — please wait a minute and try again.")
            if not config.DB_URL:
                return render_template("login.html", error="Database not configured.")
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM students WHERE email=%s", (email,))
            s = cur.fetchone(); cur.close()
            if s:
                password_ok = check_password_hash(s["password_hash"], pw)
            else:
                check_password_hash(_DUMMY_PASSWORD_HASH, pw)  # burn the same time as a real check
                password_ok = False
            if s and password_ok:
                if not s.get("is_active", True):
                    return render_template("login.html", error="This account has been suspended. Contact your administrator.")
                session.permanent = True  # actually use the 7-day PERMANENT_SESSION_LIFETIME set in create_app
                session["sid"] = s["id"]
                log_event(s["id"], "login", {"email": email})
                # Admin goes straight to analytics
                if email == config.ADMIN_EMAIL:
                    return redirect(url_for("admin.analytics_page"))
                return redirect(url_for("dashboard.dashboard"))
            return render_template("login.html", error="Invalid email or password.")
        return render_template("login.html", error=None)
    except Exception as e:
        print(f"login error: {e}"); traceback.print_exc()
        return render_template("login.html", error="Something went wrong on our end. Please try again in a moment.")


@bp.route("/logout")
def logout():
    session.clear(); return redirect(url_for("misc.landing"))


@bp.route("/verify-email/<token>")
def verify_email(token):
    """Confirms the email address behind a signup — doesn't gate access to
    WINK (the account works immediately at signup either way), it just marks
    email_verified so the admin dashboard can show which accounts have
    confirmed a reachable address."""
    if not config.DB_URL:
        return current_app.response_class("Database not configured.", mimetype="text/plain"), 500
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id FROM students WHERE verification_token=%s", (token,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return current_app.response_class(
                "This verification link is invalid or has already been used.",
                mimetype="text/plain"), 400
        cur.execute("UPDATE students SET email_verified=TRUE, verification_token=NULL WHERE id=%s", (row["id"],))
        conn.commit(); cur.close()
        log_event(row["id"], "email_verified", {})
        return current_app.response_class(
            "Your email is verified! You can close this tab and keep using WINK.",
            mimetype="text/plain")
    except Exception as e:
        print(f"verify_email error: {e}"); traceback.print_exc()
        return current_app.response_class("Something went wrong on our end. Please try again.", mimetype="text/plain"), 500


@bp.route("/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    s = g.student
    if s.get("email_verified"): return jsonify({"success": True, "already_verified": True})
    if rate_limited(f"resend-verify:{s['id']}", max_calls=3, window_seconds=300):
        return jsonify({"error": "Please wait a few minutes before requesting another email."}), 429
    try:
        token = secrets.token_urlsafe(32)
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE students SET verification_token=%s WHERE id=%s", (token, s["id"]))
        conn.commit(); cur.close()
        verify_link = url_for("auth.verify_email", token=token, _external=True)
        send_email(s["email"], "Verify your WINK email address",
                   f"Hi {s['first_name']},\n\nPlease confirm your email address by visiting:\n{verify_link}\n\n— WINK")
        return jsonify({"success": True})
    except Exception as e:
        print(f"resend_verification error: {e}"); traceback.print_exc()
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    email = request.form.get("email", "").strip().lower()
    try:
        if not email:
            return render_template("login.html", forgot=True, error="Please enter your email.")
        if rate_limited(f"forgot:{request.remote_addr}", max_calls=5, window_seconds=300):
            return render_template("login.html", forgot=True, error="Too many requests — please wait a few minutes and try again.")
        if not config.DB_URL:
            return render_template("login.html", forgot=True, error="Database not configured.")
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id FROM students WHERE email=%s", (email,))
        s = cur.fetchone()
        if not s:
            cur.close()
            # Don't reveal whether the account exists — show the same confirmation either way.
            return render_template("login.html", forgot=True, reset_sent=True)

        token = secrets.token_urlsafe(32)
        expires_at = utcnow_naive() + timedelta(hours=1)
        cur.execute(
            "INSERT INTO password_resets(student_id, token, expires_at) VALUES(%s,%s,%s)",
            (s["id"], token, expires_at)
        )
        conn.commit(); cur.close()
        log_event(s["id"], "password_reset_requested", {"email": email})

        reset_link = url_for("auth.reset_password", token=token, _external=True)
        # If no SMTP provider is configured, this falls back to logging the
        # link server-side (and only shows it in the response if
        # DEBUG_SHOW_RESET_LINKS is explicitly set for local testing) rather
        # than handing an account-takeover link to whoever submitted the form.
        sent = send_email(
            email, "Reset your WINK password",
            f"Hi,\n\nSomeone (hopefully you) requested a password reset for your WINK account.\n\n"
            f"Reset your password here: {reset_link}\n\nThis link expires in 1 hour. "
            f"If you didn't request this, you can safely ignore this email.\n\n— WINK"
        )
        if not sent:
            # Never log the actual link here — it's a working, unexpired
            # account-takeover URL for this student. If SMTP isn't
            # configured in production, resets would otherwise end up
            # sitting in server logs indefinitely, readable by anyone with
            # log access. DEBUG_SHOW_RESET_LINKS (below) is the intended,
            # explicit opt-in for local testing instead.
            print(f"forgot_password: email not sent for student {s['id']} — SMTP not configured or delivery failed")
        if not sent and config.DEBUG_SHOW_RESET_LINKS:
            return render_template("login.html", forgot=True, reset_sent=True, reset_link=reset_link)
        return render_template("login.html", forgot=True, reset_sent=True)
    except Exception as e:
        print(f"forgot_password error: {e}"); traceback.print_exc()
        return render_template("login.html", forgot=True, error="Something went wrong. Please try again.")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        if not config.DB_URL:
            return render_template("login.html", error="Database not configured.")
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT pr.id AS reset_id, pr.student_id, pr.expires_at, pr.used, s.email
            FROM password_resets pr JOIN students s ON s.id = pr.student_id
            WHERE pr.token=%s
        """, (token,))
        row = cur.fetchone()

        if not row or row["used"] or row["expires_at"] < utcnow_naive():
            cur.close()
            return render_template("reset_password.html", invalid=True)

        if request.method == "POST":
            pw = request.form.get("password", "").strip()
            confirm = request.form.get("confirm_password", "").strip()
            if len(pw) < 8:
                cur.close()
                return render_template("reset_password.html", token=token,
                                       error="Password must be at least 8 characters.")
            if pw != confirm:
                cur.close()
                return render_template("reset_password.html", token=token,
                                       error="Passwords do not match.")
            cur.execute("UPDATE students SET password_hash=%s WHERE id=%s",
                        (generate_password_hash(pw), row["student_id"]))
            cur.execute("UPDATE password_resets SET used=TRUE WHERE id=%s", (row["reset_id"],))
            conn.commit(); cur.close()
            log_event(row["student_id"], "password_reset_completed", {"email": row["email"]})
            return render_template("login.html", success="Your password has been updated — please sign in.")

        cur.close()
        return render_template("reset_password.html", token=token)
    except Exception as e:
        print(f"reset_password error: {e}"); traceback.print_exc()
        return render_template("reset_password.html", token=token, error="Something went wrong on our end. Please try again in a moment.")
