import hashlib
import json
import logging
import secrets
from datetime import timedelta

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import login_required, rate_limited
from ..services.analytics import _anonymize_student_sql, log_event
from ..services.deadlines import extract_deadlines, insert_deadlines
from ..services.email import send_email
from ..timeutil import utcnow_naive
from ..mfa_crypto import decrypt_mfa_secret, encrypt_mfa_secret
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
            if university not in config.UNIVERSITIES:
                return err("Please choose your university from the list.")
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
                if s.get("mfa_enabled"):
                    # Password alone isn't enough for this account — the
                    # session is real (sid is set) but mfa_verified is
                    # deliberately left unset, so admin_required/
                    # admin_page_required won't grant access until the
                    # code is checked at /mfa/verify.
                    return redirect(url_for("auth.mfa_verify_page"))
                if email == config.ADMIN_EMAIL:
                    return redirect(url_for("admin.analytics_page"))
                return redirect(url_for("dashboard.dashboard"))
            return render_template("landing.html", error="Invalid email or password.")
        return redirect(url_for("misc.landing"))
    except Exception as e:
        log_error("auth.login", e)
        return render_template("landing.html", error="Something went wrong on our end. Please try again in a moment.")


@bp.route("/logout", methods=["POST"])
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
            # Atomically claim the token — WHERE used=FALSE means this
            # UPDATE only actually claims it for whichever request gets
            # here first; a second, simultaneous request with the same
            # token gets 0 rows back and is correctly rejected below,
            # rather than the earlier pattern where two requests could
            # both pass a separate "is it used?" check before either had
            # committed marking it used. Also re-checks expiry at the
            # same atomic instant, rather than relying on the earlier
            # read further up which could be stale by now.
            cur.execute("""UPDATE password_resets SET used=TRUE
                           WHERE id=%s AND used=FALSE AND expires_at > NOW()
                           RETURNING student_id""", (row["reset_id"],))
            claimed = cur.fetchone()
            if not claimed:
                conn.commit(); cur.close()
                return render_template("landing.html", show_reset_modal=True, reset_password_invalid=True)
            cur.execute("UPDATE students SET password_hash=%s, password_changed_at=NOW() WHERE id=%s",
                        (generate_password_hash(pw), claimed["student_id"]))
            conn.commit(); cur.close()
            log_event(claimed["student_id"], "password_reset_completed")
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
    security.py/calendar.py/documents.py), and atomically anonymizes
    their identifying information (name, email) in the SAME transaction
    — see _anonymize_student_sql() in services/analytics.py for exactly
    what that does and doesn't scrub.

    These two things happen in one transaction on purpose: if they were
    two separate commits (as this used to be) and the second one failed
    for any reason, the account would end up disabled — the student
    logged out, unable to log back in — while their real name and email
    were still sitting in the database, un-anonymized. Either both
    changes land together, or neither does; there's no partial state
    where deletion "succeeded" from the account's perspective but the
    anonymization it promised silently didn't happen.

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
        _anonymize_student_sql(cur, s["id"])
        conn.commit(); cur.close()
        log_event(s["id"], "account_deleted")
        session.clear()
        return jsonify({"ok": True})
    except Exception as e:
        log_error("auth.delete_account", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


# --- Two-factor authentication (TOTP) ---------------------------------
#
# Admin access was otherwise gated purely by "does the logged-in email
# match ADMIN_EMAIL" — a single password standing between anyone and
# full access to (anonymized, but still real) student research data.
# This adds a second factor: an authenticator-app code, checked at
# /mfa/verify after a normal password login, before admin_required or
# admin_page_required (see security.py) will grant access. The
# mechanism itself isn't hard-restricted to the admin account — any
# account can enable it — but enforcement in security.py only actually
# blocks anything for an account that has mfa_enabled=True, so today
# that's effectively just whichever account you turn it on for.


def _generate_backup_codes(n=8):
    """Returns (plain_codes_to_show_once, hashed_codes_to_store)."""
    plain = [secrets.token_hex(4) for _ in range(n)]
    hashed = [generate_password_hash(c) for c in plain]
    return plain, hashed


@bp.route("/mfa/setup", methods=["GET", "POST"])
@login_required
def mfa_setup():
    import pyotp
    s = g.student
    if request.method == "GET":
        if s.get("mfa_enabled"):
            return render_template("mfa_setup.html", already_enabled=True)
        # A pending secret lives only in the session until confirmed —
        # never written to the database until the user proves they can
        # actually generate a valid code with it. Reused across repeated
        # GETs during setup so the QR code and the code they eventually
        # submit refer to the same secret.
        if not session.get("mfa_pending_secret"):
            session["mfa_pending_secret"] = pyotp.random_base32()
        secret = session["mfa_pending_secret"]
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(name=s["email"], issuer_name="WINK Admin")
        return render_template("mfa_setup.html", already_enabled=False,
                                secret=secret, provisioning_uri=provisioning_uri)

    # POST — verify the code against the pending secret, then enable
    code = request.form.get("code", "").strip()
    secret = session.get("mfa_pending_secret")
    if not secret:
        return render_template("mfa_setup.html", already_enabled=False,
                                error="Your setup session expired — please start again.")
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        provisioning_uri = pyotp.TOTP(secret).provisioning_uri(name=s["email"], issuer_name="WINK Admin")
        return render_template("mfa_setup.html", already_enabled=False,
                                secret=secret, provisioning_uri=provisioning_uri,
                                error="That code didn't match — check your authenticator app and try again.")
    plain_codes, hashed_codes = _generate_backup_codes()
    conn = get_db(); cur = conn.cursor()
    cur.execute("""UPDATE students SET mfa_secret=%s, mfa_enabled=TRUE, mfa_backup_codes=%s
                   WHERE id=%s""", (encrypt_mfa_secret(secret), json.dumps(hashed_codes), s["id"]))
    conn.commit(); cur.close()
    session.pop("mfa_pending_secret", None)
    session["mfa_verified"] = True  # they just proved possession — no need to re-enter immediately
    log_event(s["id"], "mfa_enabled")
    return render_template("mfa_setup.html", already_enabled=True, just_enabled=True,
                            backup_codes=plain_codes)


@bp.route("/mfa/qr-code")
@login_required
def mfa_qr_code():
    import io
    import pyotp
    import qrcode
    s = g.student
    secret = session.get("mfa_pending_secret")
    if not secret:
        abort(404)
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=s["email"], issuer_name="WINK Admin")
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return current_app.response_class(buf.read(), mimetype="image/png")


@bp.route("/mfa/verify", methods=["GET", "POST"])
@login_required
def mfa_verify_page():
    import pyotp
    s = g.student
    if not s.get("mfa_enabled"):
        # Nothing to verify for this account — don't leave them stuck
        # on a page that can never succeed.
        return redirect(url_for("dashboard.dashboard"))
    if request.method == "GET":
        return render_template("mfa_verify.html")

    code = request.form.get("code", "").strip()
    if rate_limited(f"mfa-verify:{s['id']}", max_calls=5, window_seconds=60):
        return render_template("mfa_verify.html", error="Too many attempts — please wait a minute and try again.")

    verified = False
    if s.get("mfa_secret"):
        totp = pyotp.TOTP(decrypt_mfa_secret(s["mfa_secret"]))
        verified = totp.verify(code, valid_window=1)

    if not verified and code:
        # Fall back to checking backup codes — each one is single-use,
        # for when the authenticator device itself isn't available.
        # SELECT ... FOR UPDATE locks this row until the transaction
        # commits below, so a second simultaneous request using the same
        # code has to wait for this one to finish first, rather than
        # both reading the same pre-request snapshot of the code list
        # (via `s`, fetched before this request even started) and both
        # independently believing they'd successfully consumed it.
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT mfa_backup_codes FROM students WHERE id=%s FOR UPDATE", (s["id"],))
        locked_row = cur.fetchone()
        try:
            hashed_codes = json.loads((locked_row or {}).get("mfa_backup_codes") or "[]")
        except Exception:
            hashed_codes = []
        for i, h in enumerate(hashed_codes):
            if check_password_hash(h, code):
                verified = True
                remaining = hashed_codes[:i] + hashed_codes[i + 1:]
                cur.execute("UPDATE students SET mfa_backup_codes=%s WHERE id=%s",
                            (json.dumps(remaining), s["id"]))
                log_event(s["id"], "mfa_backup_code_used")
                break
        # Commits (releasing the row lock) whether or not a match was
        # found — an unmatched attempt didn't change anything, but the
        # SELECT ... FOR UPDATE above still needs its transaction closed
        # out promptly rather than held open for the rest of the request.
        conn.commit(); cur.close()

    if not verified:
        return render_template("mfa_verify.html", error="That code wasn't right — please try again.")

    session["mfa_verified"] = True
    log_event(s["id"], "mfa_verified")
    if s["email"].lower() == config.ADMIN_EMAIL:
        return redirect(url_for("admin.analytics_page"))
    return redirect(url_for("dashboard.dashboard"))


@bp.route("/mfa/disable", methods=["POST"])
@login_required
def mfa_disable():
    s = g.student
    if not s.get("mfa_enabled"):
        return jsonify({"error": "MFA isn't enabled on this account."}), 400
    # Requires BOTH an already-MFA-verified session AND the password
    # again — someone who only has the password (the exact thing MFA is
    # meant to add a layer beyond) shouldn't be able to turn it back off.
    if not session.get("mfa_verified"):
        return jsonify({"error": "Please verify your current code before disabling MFA."}), 403
    pw = request.form.get("password", "").strip()
    if not pw or not check_password_hash(s["password_hash"], pw):
        return jsonify({"error": "Incorrect password."}), 400
    conn = get_db(); cur = conn.cursor()
    cur.execute("""UPDATE students SET mfa_secret=NULL, mfa_enabled=FALSE, mfa_backup_codes='[]'
                   WHERE id=%s""", (s["id"],))
    conn.commit(); cur.close()
    log_event(s["id"], "mfa_disabled")
    return jsonify({"ok": True})
