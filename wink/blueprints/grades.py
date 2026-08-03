"""
Grade calculator: a dedicated page (not just a chat capability). A
first-semester student is both the least likely to know to ask the right
chat question and the least equipped to catch a subtly wrong answer, so a
labeled tool with visible, checkable numbers fits better than a prompt
instruction alone — see services/grades.py for the fuller reasoning.

NOTE: rebuilt from a description of an earlier version of this file (the
original was written in a different conversation and isn't available to
copy from directly) — routes/behavior match what was documented at the
time, but treat this as a fresh implementation, not a byte-for-byte
restore.
"""
import traceback

from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..security import login_required, page_login_required, rate_limited, verified_required
from ..services.analytics import log_event
from ..services.documents import get_docs
from ..services.grades import extract_grading_weights, get_grading_weights, store_grading_weights

bp = Blueprint("grades", __name__)


@bp.route("/grades-page")
@page_login_required
def grades_page():
    try:
        s = g.student
        docs = get_docs(s["id"])
        known_courses = sorted({(d.get("course") or "").strip() for d in docs if (d.get("course") or "").strip()})
        log_event(s["id"], "page_view", {"page": "grades"})
        return render_template("grades.html", s=s, admin_email=config.ADMIN_EMAIL,
                               active="grades", known_courses=known_courses)
    except Exception as e:
        print(f"grades_page error: {e}"); traceback.print_exc()
        return "<h2>Something went wrong</h2><p>Please try again, or <a href='/logout'>log out</a> and back in.</p>", 500


@bp.route("/grading-weights")
@login_required
def grading_weights():
    """Returns this student's stored weights for a course — [] if nothing
    has been extracted or saved yet."""
    s = g.student
    course = (request.args.get("course") or "").strip()
    if not course:
        return jsonify({"error": "course is required"}), 400
    return jsonify({"weights": get_grading_weights(s["id"], course)})


@bp.route("/extract-grading-weights", methods=["POST"])
@login_required
@verified_required
def extract_grading_weights_route():
    """Extracts a course's grading breakdown from that course's own
    uploaded material (combined, like /generate-practice does) and stores
    it — a real, non-trivial model call, so rate-limited the same way
    practice-question generation is."""
    s = g.student
    data = request.get_json(silent=True) or {}
    course = (data.get("course") or "").strip()
    if not course:
        return jsonify({"error": "course is required"}), 400

    wait = rate_limited(f"extract-grades:{s['id']}", max_calls=10, window_seconds=3600)
    if wait:
        return jsonify({"error": "Please slow down a bit before extracting again.", "retry_after": wait}), 429

    docs = [d for d in get_docs(s["id"]) if (d.get("course") or "").strip().lower() == course.lower()]
    if not docs:
        return jsonify({"error": "No uploaded documents found for this course yet. Upload a syllabus on the Documents page first."}), 400
    combined = "\n\n".join((d.get("content") or "") for d in docs)[:config.PRACTICE_MATERIAL_MAX_CHARS]
    if not combined.strip():
        return jsonify({"error": "Your uploaded documents for this course have no extractable text."}), 400

    weights = extract_grading_weights(combined)
    if not weights:
        return jsonify({
            "weights": [],
            "message": "Couldn't find a clear grading breakdown in your uploaded material for "
                       "this course — you can still add categories manually below.",
        })
    store_grading_weights(s["id"], course, weights)
    log_event(s["id"], "grading_weights_extracted", {"course": course, "count": len(weights)})
    return jsonify({"weights": weights})


@bp.route("/save-grading-weights", methods=["POST"])
@login_required
def save_grading_weights_route():
    """Persists a student's edited weight table — the same
    store_grading_weights() the extract route uses, since editing the
    table (adding/removing/renaming a category, fixing a percentage) is
    replacing the whole breakdown, not patching one field. Scores are
    intentionally NOT accepted here — see grades.html, they're kept in the
    browser's own localStorage since they're scratch-pad what-if data, not
    the actual grading scheme."""
    s = g.student
    data = request.get_json(silent=True) or {}
    course = (data.get("course") or "").strip()
    weights = data.get("weights")
    if not course or not isinstance(weights, list):
        return jsonify({"error": "course and weights are required"}), 400
    store_grading_weights(s["id"], course, weights)
    return jsonify({"ok": True})
