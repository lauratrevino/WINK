import hashlib
import logging
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import config
from ..errors import log_error
from ..extensions import csrf, get_db
from ..security import login_required, rate_limited
from ..services.analytics import anonymize_student_record, log_event
from ..services.deadlines import extract_deadlines, insert_deadlines
from ..services.email import send_email
from ..timeutil import utcnow_naive
from .demo import delete_demo_student

bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)

_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_hex(32))


@bp.route("/register", methods=["GET", "POST"])
def register():
    def err(msg):
        return render_template("register.html", error=msg,
                               classifications=config.CLASSIFICATIONS, majors=config.MAJORS,
                               preferred_languages=config.PREFERRED_LANGUAGES)
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
            preferred_language = request.form.get("preferred_language", "").strip()
            terms_agree = request.form.get("terms_agree") == "on"
            research_agree = request.form.get("research_agree") == "on"
            if not all([email, pw, fn, ln, cl, major, university]):
                return err("All fields are required, including your university.")
            if not (terms_agree and research_agree):
                return err("You must agree to the Terms of Use/Privacy Policy and the research data notice to create an account.")
            if preferred_language and preferred_language not in config.PREFERRED_LANGUAGES:
                return err("Please choose a valid preferred language.")
            if cl not in config.CLASSIFICATIONS or major not in config.MAJORS:
                return err("Please choose a valid classification and major.")
            if not config.EMAIL_RE.match(email):
                return err("Please enter a valid email address.")
            if len(pw) < 8:
                return err("Password must be at least 8 characters.")
            if not config.DB_URL:
                return err("Database not configured.")
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", (email,))
            if cur.fetchone():
                cur.close()
                return err("Account already exists — please log in.")
            cur.execute("""INSERT INTO students(email,password_hash,first_name,last_name,classification,major,university,preferred_language,
                           terms_accepted_at,terms_version,research_consent,research_consent_at,research_consent_version)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,NOW(),%s) RETURNING id""",
                        (email, generate_password_hash(pw), fn, ln, cl, major, university, preferred_language,
                         config.TERMS_VERSION, research_agree, config.TERMS_VERSION))
            new_id = cur.fetchone()["id"]
            verify_token = secrets.token_urlsafe(32)
            cur.execute("UPDATE students SET verification_token=%s WHERE id=%s", (verify_token, new_id))
            conn.commit(); cur.close()

            # From here on, the account is fully created and functional — nothing
            # below this point should be able to turn a successful registration
            # into an "Something went wrong" error for the student. Log them in
            # and send their verification email first (the actual promise this
            # endpoint makes), then attempt the optional global-deadline
            # assignment separately, swallowing any failure there so it can
            # only ever cost them pre-populated deadlines, never the account
            # itself or their session.
            session.permanent = True
            session["sid"] = new_id
            session["pw_changed_at"] = None  # freshly created — no password change yet
            log_event(new_id, "account_created", {"classification": cl, "major": major, "university": university})
            verify_link = url_for("auth.verify_email", token=verify_token, _external=True)
            email_sent = send_email(email, "Verify your WINK email address",
                       f"Hi {fn},\n\nWelcome to WINK! Please confirm your email address by visiting:\n{verify_link}\n\n"
                       f"You'll need to verify before you can chat with WINK or upload documents — "
                       f"it only takes a moment.\n\n— WINK")
            if not email_sent:
                if config.EMAIL_CONFIGURED:
                    # SMTP is configured but the send itself failed — this is a real
                    # operational problem (bad credentials, provider outage, etc.),
                    # distinct from the expected "not configured" case, which the
                    # health page already reports separately. Surface it loudly.
                    log_error("auth.register.verification_email",
                              RuntimeError("send_email() returned False despite EMAIL_CONFIGURED=True"),
                              student_id=new_id, email=email)
                else:
                    logger.warning(
                        "Registered %s but SMTP isn't configured, so no verification "
                        "email was sent — student is stuck until this is fixed or they "
                        "use Resend after SMTP is configured.", email
                    )

            try:
                conn = get_db(); cur = conn.cursor()
                cur.execute("""SELECT DISTINCT ON (dl.document_id, dl.title, dl.due_date)
                               dl.document_id, dl.course, dl.title, dl.due_date, dl.source_snippet
                               FROM deadlines dl
                               JOIN documents d ON d.id = dl.document_id
                               WHERE d.student_id IS NULL AND (lower(d.university)=lower(%s) OR lower(d.university)='all')
                               ORDER BY dl.document_id, dl.title, dl.due_date""",
                            (university,))
                global_deadlines = [dict(r) for r in cur.fetchall()]
                cur.close()
                for r in global_deadlines:
                    due = r["due_date"]
                    due_str = due.isoformat() if hasattr(due, "isoformat") else due
                    insert_deadlines(new_id, r["document_id"], r["course"],
                                     [{"title": r["title"], "due_date": due_str,
                                       "source_snippet": r.get("source_snippet", "")}])

                covered_doc_ids = {r["document_id"] for r in global_deadlines}
                conn = get_db(); cur = conn.cursor()
                cur.execute("""SELECT id, course, content FROM documents
                               WHERE student_id IS NULL AND (lower(university)=lower(%s) OR lower(university)='all')
                               AND content IS NOT NULL AND content != ''""",
                            (university,))
                orphan_docs = [dict(r) for r in cur.fetchall() if dict(r)["id"] not in covered_doc_ids]
                cur.close()
                for doc in orphan_docs:
                    deadlines = extract_deadlines(doc["content"])
                    if deadlines:
                        insert_deadlines(new_id, doc["id"], doc["course"], deadlines)
            except Exception as e:
                log_error("auth.register.global_deadline_assignment", e)

            return redirect(url_for("documents.documents_page"))
        return render_template("register.html", error=None,
                               classifications=config.CLASSIFICATIONS, majors=config.MAJORS,
                               preferred_languages=config.PREFERRED_LANGUAGES)
    except Exception as e:
        log_error("auth.register", e)
        return err("Something went wrong on our end. Please try again in a moment.")


@bp.route("/login", methods=["GET", "POST"])
def login():
    try:
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "").strip()
            if rate_limited(f"login:{request.remote_addr}", max_calls=10, window_seconds=60):
                return render_template("landing.html", error="Too many attempts — please wait a minute and try again.")
            if not config.DB_URL:
                return render_template("landing.html", error="Database not configured.")
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM students WHERE email=%s", (email,))
            s = cur.fetchone(); cur.close()
            if s:
                password_ok = check_password_hash(s["password_hash"], pw)
            else:
                check_password_hash(_DUMMY_PASSWORD_HASH, pw)  
                password_ok = False
            if s and password_ok:
                if s.get("is_active") is False:
                    return render_template("landing.html", error="This account has been suspended. Contact your administrator.")
                if s.get("account_deleted_at") is not None:
                    return render_template("landing.html", error="This account has been deleted.")
                session.permanent = True  
                session["sid"] = s["id"]
                pw_changed = s.get("password_changed_at")
                session["pw_changed_at"] = pw_changed.isoformat() if pw_changed else None
                log_event(s["id"], "login")
                if email == config.ADMIN_EMAIL:
                    return redirect(url_for("admin.analytics_page"))
                return redirect(url_for("dashboard.dashboard"))
            return render_template("landing.html", error="Invalid email or password.")
        return redirect(url_for("misc.landing"))
    except Exception as e:
        log_error("auth.login", e)
        return render_template("landing.html", error="Something went wrong on our end. Please try again in a moment.")


@bp.route("/logout", methods=["POST"])
@csrf.exempt
def logout():
    sid = session.get("sid")
    is_demo = session.get("is_demo")
    if sid and is_demo and config.DB_URL:
        try:
            delete_demo_student(sid, reason="logout")
        except Exception as e:
            log_error("auth.logout_demo_cleanup", e)
    session.clear(); return redirect(url_for("misc.landing"))


@bp.route("/verify-email/<token>")
def verify_email(token):
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
        log_error("auth.verify_email", e)
        return current_app.response_class("Something went wrong on our end. Please try again.", mimetype="text/plain"), 500


@bp.route("/verification-status")
@login_required
def verification_status():
    return jsonify({"email_verified": bool(g.student.get("email_verified"))})


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
        sent = send_email(s["email"], "Verify your WINK email address",
                   f"Hi {s['first_name']},\n\nPlease confirm your email address by visiting:\n{verify_link}\n\n— WINK")
        if not sent:
            if config.EMAIL_CONFIGURED:
                log_error("auth.resend_verification.send_failed",
                          RuntimeError("send_email() returned False despite EMAIL_CONFIGURED=True"),
                          student_id=s["id"])
            else:
                logger.warning("resend_verification: SMTP not configured, no email sent for student %s", s["id"])
            return jsonify({"error": "We couldn't send the verification email right now. "
                                      "Please try again in a few minutes, or contact your administrator "
                                      "if this keeps happening."}), 502
        return jsonify({"success": True})
    except Exception as e:
        log_error("auth.resend_verification", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    email = request.form.get("email", "").strip().lower()
    try:
        if not email:
            return render_template("landing.html", forgot=True, error="Please enter your email.")
        if rate_limited(f"forgot:{request.remote_addr}", max_calls=5, window_seconds=300):
            return render_template("landing.html", forgot=True, error="Too many requests — please wait a few minutes and try again.")
        if not config.DB_URL:
            return render_template("landing.html", forgot=True, error="Database not configured.")
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id FROM students WHERE email=%s", (email,))
        s = cur.fetchone()
        if not s:
            cur.close()
            return render_template("landing.html", forgot=True, reset_sent=True)

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at = utcnow_naive() + timedelta(hours=1)
        cur.execute(
            "INSERT INTO password_resets(student_id, token, expires_at) VALUES(%s,%s,%s)",
            (s["id"], token_hash, expires_at)
        )
        conn.commit(); cur.close()
        log_event(s["id"], "password_reset_requested")

        reset_link = url_for("auth.reset_password", token=token, _external=True)
        sent = send_email(
            email, "Reset your WINK password",
            f"Hi,\n\nSomeone (hopefully you) requested a password reset for your WINK account.\n\n"
            f"Reset your password here: {reset_link}\n\nThis link expires in 1 hour. "
            f"If you didn't request this, you can safely ignore this email.\n\n— WINK"
        )
        if not sent:
            logger.warning("forgot_password: email not sent for student %s — SMTP not configured or delivery failed", s['id'])
        if not sent and config.DEBUG_SHOW_RESET_LINKS:
            return render_template("landing.html", forgot=True, reset_sent=True, reset_link=reset_link)
        return render_template("landing.html", forgot=True, reset_sent=True)
    except Exception as e:
        log_error("auth.forgot_password", e)
        return render_template("landing.html", forgot=True, error="Something went wrong. Please try again.")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        if not config.DB_URL:
            return render_template("landing.html", show_reset_modal=True, reset_password_error="Database not configured.")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT pr.id AS reset_id, pr.student_id, pr.expires_at, pr.used, s.email
            FROM password_resets pr JOIN students s ON s.id = pr.student_id
            WHERE pr.token=%s
        """, (token_hash,))
        row = cur.fetchone()

        if not row or row["used"] or row["expires_at"] < utcnow_naive():
            cur.close()
            return render_template("landing.html", show_reset_modal=True, reset_password_invalid=True)

        if request.method == "POST":
            pw = request.form.get("password", "").strip()
            confirm = request.form.get("confirm_password", "").strip()
            if len(pw) < 8:
                cur.close()
                return render_template("landing.html", show_reset_modal=True, reset_password_token=token,
                                       reset_password_error="Password must be at least 8 characters.")
            if pw != confirm:
                cur.close()
                return render_template("landing.html", show_reset_modal=True, reset_password_token=token,
                                       reset_password_error="Passwords do not match.")
            cur.execute("UPDATE students SET password_hash=%s, password_changed_at=NOW() WHERE id=%s",
                        (generate_password_hash(pw), row["student_id"]))
            cur.execute("UPDATE password_resets SET used=TRUE WHERE id=%s", (row["reset_id"],))
            conn.commit(); cur.close()
            log_event(row["student_id"], "password_reset_completed")
            return render_template("landing.html", success="Your password has been updated — please sign in.")

        cur.close()
        return render_template("landing.html", show_reset_modal=True, reset_password_token=token)
    except Exception as e:
        log_error("auth.reset_password", e)
        return render_template("landing.html", show_reset_modal=True, reset_password_token=token,
                               reset_password_error="Something went wrong on our end. Please try again in a moment.")


@bp.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    """Deletes the student's own account: blocks login immediately (via
    account_deleted_at, same as before — this also excludes them from
    deadline reminders and other ongoing automated processes, see
    security.py/calendar.py/documents.py), and additionally anonymizes
    their identifying information (name, email) using the same routine as
    the admin-triggered anonymization action — see
    anonymize_student_record() in services/analytics.py for exactly what
    that does and doesn't scrub.

    This is a small research pilot (fewer than 10 students) with a
    deliberately simple policy: deleting your account removes your ability
    to log in and identifies you as "Participant-xxxx" going forward, but
    the (now-anonymized) activity data itself is retained for the research
    study rather than purged, consistent with the consent given at
    registration and described in the Privacy Policy."""
    s = g.student
    if not config.DB_URL:
        return jsonify({"error": "No database"}), 500
    pw = request.form.get("password", "").strip()
    if not pw or not check_password_hash(s["password_hash"], pw):
        return jsonify({"error": "Incorrect password."}), 400
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE students SET account_deleted_at=NOW() WHERE id=%s AND account_deleted_at IS NULL",
                    (s["id"],))
        conn.commit(); cur.close()
        anonymize_student_record(s["id"])
        log_event(s["id"], "account_deleted")
        session.clear()
        return jsonify({"ok": True})
    except Exception as e:
        log_error("auth.delete_account", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
