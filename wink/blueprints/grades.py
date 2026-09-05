import math

from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..errors import log_error
from ..extensions import generate_csrf_token
from ..security import login_required, page_login_required, rate_limited, verified_required
from ..services.analytics import log_event
from ..services.documents import get_docs
from ..services.grades import extract_grading_weights, get_grading_weights, store_grading_weights

bp = Blueprint("grades", __name__)

# Categories above this count are rejected outright rather than silently
# truncated — matches the cap extract_grading_weights() already applies to
# AI-extracted weights (services/grades.py), so a manually-built list can't
# exceed what an extracted one ever could.
MAX_GRADING_CATEGORIES = 20
# Total-weight tolerance: real syllabi occasionally round to e.g.
# 33.33/33.33/33.34 = 100.0 exactly, but 0.01 absorbs only genuine
# floating-point rounding, not a mistyped total — the max is 100%, not
# 100.5%, and the error message says so.
GRADING_TOTAL_TOLERANCE = 0.01


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
        log_error("grades.grades_page", e)
        return f"<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><input type='hidden' name='csrf_token' value='{generate_csrf_token()}'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/grading-weights")
@login_required
def grading_weights():
    s = g.student
    course = (request.args.get("course") or "").strip()
    if not course:
        return jsonify({"error": "course is required"}), 400
    return jsonify({"weights": get_grading_weights(s["id"], course)})


@bp.route("/extract-grading-weights", methods=["POST"])
@login_required
@verified_required
def extract_grading_weights_route():
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

    weights = extract_grading_weights(combined, student_id=s["id"])
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
    s = g.student
    data = request.get_json(silent=True) or {}
    course = (data.get("course") or "").strip()
    weights = data.get("weights")
    if not course or not isinstance(weights, list):
        return jsonify({"error": "course and weights are required"}), 400
    # An empty list is valid input, not an omission — it means "clear this
    # course's grading weights," which the calculator UI relies on (e.g.
    # switching a course back to no breakdown). Nothing below this needs
    # to run for that case.
    if not weights:
        store_grading_weights(s["id"], course, [])
        return jsonify({"ok": True, "cleared": True})
    if len(weights) > MAX_GRADING_CATEGORIES:
        return jsonify({"error": f"No more than {MAX_GRADING_CATEGORIES} grading categories are supported."}), 400
    total = 0.0
    seen_categories = set()
    for w in weights:
        if not isinstance(w, dict):
            return jsonify({"error": "Invalid weight entry."}), 400
        category = str(w.get("category", "")).strip()
        if not category:
            return jsonify({"error": "Each category needs a name."}), 400
        if category[:100].lower() in seen_categories:
            return jsonify({"error": f"'{category[:100]}' is listed more than once."}), 400
        seen_categories.add(category[:100].lower())
        try:
            value = float(w.get("weight"))
        except (TypeError, ValueError):
            return jsonify({"error": "Each weight must be a number."}), 400
        # isfinite rejects NaN and +/-Infinity outright — plain `<= 0` /
        # `> 100` comparisons are always False against NaN, so NaN was
        # previously passing this check, being summed into `total` (which
        # then becomes NaN itself and fails the `> ` total check too), and
        # reaching store_grading_weights(), which silently dropped it
        # (its own `weight > 0` check is also False for NaN) — the net
        # effect was a 200 "ok" response after deleting the student's
        # existing weights and inserting nothing in their place.
        if not math.isfinite(value) or value <= 0 or value > 100:
            return jsonify({"error": "Each weight must be a number greater than 0 and no more than 100."}), 400
        total += value
    if total > 100 + GRADING_TOTAL_TOLERANCE:
        return jsonify({"error": "Weights add up to more than 100%. Please adjust before saving."}), 400
    store_grading_weights(s["id"], course, weights)
    return jsonify({"ok": True})
