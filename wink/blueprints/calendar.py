import concurrent.futures

from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..errors import log_error
from ..extensions import csrf, get_db
from ..security import login_required, page_login_required, rate_limited
from ..services.analytics import log_event
from ..services.deadlines import build_study_plan, detect_deadline_conflicts, extract_deadlines, get_all_deadlines, get_upcoming_deadlines
from ..services.documents import get_docs
from ..services.email import send_email

bp = Blueprint("calendar", __name__)

# Bounded concurrency for reprocess_deadlines()'s parallel extract_deadlines()
# calls below — enough to meaningfully cut wall-clock time on a student with
# many documents, not so many that one student's reprocess run monopolizes
# the Anthropic connection pool (extensions.py caps that at 50 total).
MAX_REPROCESS_WORKERS = 5


@bp.route("/deadlines")
@login_required
def deadlines():
    s = g.student
    days = min(int(request.args.get("days", 14)), 90)
    return jsonify({"deadlines": get_upcoming_deadlines(s["id"], days)})


@bp.route("/calendar-page")
@page_login_required
def calendar_page():
    s = g.student
    log_event(s["id"], "page_view", {"page": "calendar"})
    return render_template("calendar.html", s=s, admin_email=config.ADMIN_EMAIL, active="calendar")


@bp.route("/calendar-data")
@login_required
def calendar_data():
    s = g.student
    return jsonify({"deadlines": get_all_deadlines(s["id"])})


@bp.route("/deadline-conflicts")
@login_required
def deadline_conflicts():
    """Clusters of deadlines landing close together across the student's
    courses — see detect_deadline_conflicts()'s docstring for exactly what
    this does and doesn't detect. No dashboard widget renders this yet
    (see README); it's available for one to be built against."""
    s = g.student
    return jsonify({"conflicts": detect_deadline_conflicts(s["id"])})


@bp.route("/study-plan")
@login_required
def study_plan():
    """Week-by-week plan combining upcoming deadlines with practice
    questions due for review — see build_study_plan()'s docstring."""
    s = g.student
    weeks = request.args.get("weeks", 4, type=int)
    return jsonify({"weeks": build_study_plan(s["id"], weeks_ahead=max(1, min(weeks, 12)))})


@bp.route("/reprocess-deadlines", methods=["POST"])
@login_required
def reprocess_deadlines():
    """Re-run deadline extraction against documents that are already stored
    (using their already-extracted, already-stored text — no re-upload
    needed). Useful for documents uploaded before DEADLINE_EXTRACTION_MAX_CHARS
    was fixed, or if a document's schedule wasn't picked up the first time."""
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    if rate_limited(f"reprocess:{s['id']}", max_calls=3, window_seconds=300):
        return jsonify({"error": "Please wait a few minutes before doing this again."}), 429
    try:
        docs = get_docs(s["id"])
        docs_with_content = [d for d in docs if (d.get("content") or "").strip()]

        # extract_deadlines() is a network call to Anthropic — purely
        # I/O-bound (waiting on the response, not burning CPU) — so running
        # several concurrently instead of one-by-one cuts wall-clock time
        # roughly by a factor of MAX_REPROCESS_WORKERS instead of it scaling
        # linearly with document count. A student at the 20-document cap
        # processed sequentially could take long enough to risk hitting
        # gunicorn's request timeout; this keeps it well clear of that.
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_REPROCESS_WORKERS) as pool:
            future_to_doc = {
                pool.submit(extract_deadlines, (d.get("content") or "").strip()): d
                for d in docs_with_content
            }
            for future in concurrent.futures.as_completed(future_to_doc):
                d = future_to_doc[future]
                try:
                    results[d["id"]] = future.result()
                except Exception as e:
                    log_error("calendar.reprocess_deadlines_extract", e, doc_id=d['id'])
                    results[d["id"]] = []

        # DB writes happen afterward, sequentially, in one connection/one
        # transaction — psycopg2 connections aren't safe to share across the
        # threads above, and there's no need to: the slow part (waiting on
        # the model) is already done concurrently by this point.
        total_found = 0
        docs_processed = 0
        docs_skipped_empty = 0
        conn = get_db(); cur = conn.cursor()
        for d in docs_with_content:
            found = results.get(d["id"], [])
            if not found:
                # Don't delete previously-extracted deadlines just because this
                # run came back empty — that's more likely a transient API
                # hiccup or model variance than "there are actually now zero
                # deadlines in this document." Leave existing data alone.
                docs_skipped_empty += 1
                continue
            # Replace this document's deadlines rather than duplicating them —
            # only reached when we have new results to replace them WITH.
            cur.execute("DELETE FROM deadlines WHERE document_id=%s", (d["id"],))
            for item in found:
                cur.execute("""INSERT INTO deadlines(student_id,document_id,course,title,due_date)
                               VALUES(%s,%s,%s,%s,%s)""",
                            (s["id"], d["id"], d["course"], item["title"], item["due_date"]))
            docs_processed += 1
            total_found += len(found)
        conn.commit(); cur.close()
        log_event(s["id"], "deadlines_reprocessed", {"docs": docs_processed, "found": total_found, "skipped_empty": docs_skipped_empty})
        return jsonify({"success": True, "documents_processed": docs_processed, "deadlines_found": total_found})
    except Exception as e:
        log_error("calendar.reprocess_deadlines", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/send-deadline-reminders", methods=["POST"])
@csrf.exempt  # called by an external scheduler with ?key=CRON_SECRET, not a browser session — it has no CSRF token to send
def send_deadline_reminders():
    """Meant to be hit once a day by an external scheduler (Render cron job,
    GitHub Action, etc.) with ?key=CRON_SECRET — emails each student a
    summary of anything due in the next 3 days that they haven't already
    been reminded about."""
    if not config.CRON_SECRET or request.args.get("key") != config.CRON_SECRET:
        return jsonify({"error": "Not authorized"}), 403
    if not config.DB_URL:
        return jsonify({"error": "No database"}), 500
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT d.id, d.title, d.due_date, d.course, s.id as sid, s.email, s.first_name
                       FROM deadlines d JOIN students s ON s.id = d.student_id
                       WHERE d.reminded = FALSE
                       AND d.due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 3
                       ORDER BY s.id, d.due_date""")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()

        by_student = {}
        for r in rows:
            by_student.setdefault(r["sid"], {"email": r["email"], "first_name": r["first_name"], "items": []})
            by_student[r["sid"]]["items"].append(r)

        sent_count = 0
        reminded_ids = []
        for sid, info in by_student.items():
            lines = [f"  • {it['title']} ({it['course']}) — due {it['due_date'].strftime('%A, %b %d')}"
                     for it in info["items"]]
            body = (f"Hi {info['first_name']},\n\nHere's what's coming up in the next few days:\n\n"
                    + "\n".join(lines) + "\n\n— WINK")
            # Only mark THIS student's deadlines as reminded if their email
            # actually sent. A transient SMTP failure used to mark every
            # student's deadlines as reminded regardless of delivery
            # success, permanently suppressing future reminders for anyone
            # caught by that failure (since the next run's query only looks
            # at reminded=FALSE rows) — real bug, fixed here.
            if send_email(info["email"], "Upcoming deadlines — WINK", body):
                sent_count += 1
                reminded_ids.extend(it["id"] for it in info["items"])

        if reminded_ids:
            conn = get_db(); cur = conn.cursor()
            cur.execute("UPDATE deadlines SET reminded=TRUE WHERE id = ANY(%s)", (reminded_ids,))
            conn.commit(); cur.close()

        return jsonify({"students_notified": sent_count, "deadlines_covered": len(rows)})
    except Exception as e:
        log_error("calendar.send_deadline_reminders", e)
        return jsonify({"error": "Something went wrong on our end."}), 500
