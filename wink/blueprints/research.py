"""
Admin-only research dashboard. Read-only except for /research/rate-answer,
which lets a faculty reviewer score a sample of real answers correct /
incorrect / unsure — the accuracy-evaluation step the July 2026 external
WINK review found missing. Nothing here changes what a student sees; this
is instrumentation and review tooling for the people running the research
pilot.

Wire-up needed elsewhere (not in this file, since those routes live in
blueprints/chat.py and blueprints/documents.py, which weren't part of this
change):

  1. In blueprints/chat.py's /chat route, right after the model response is
     complete, call:

         from ..services.research import log_answer
         log_answer(
             student_id=g.student["id"],
             question=user_message,
             answer_text=full_response_text,
             conversation_id=conversation_id,          # or None
             message_index=len(saved_messages) - 1,    # this answer's index in conversation.messages
             retrieval_backend="neural" if used_neural else ("tfidf" if used_retrieval else "full_context"),
             chunk_count=len(top_chunks) if used_retrieval else 0,
             document_ids=[d["id"] for d in docs],
             latency_ms=int((time.time() - start_time) * 1000),
         )

     `used_neural`/`used_retrieval`/`top_chunks` are whatever build_doc_context()
     already computed for that turn — this just records them instead of
     discarding them once the prompt is built.

     Then in the existing /rate-answer route (the thumbs up/down endpoint
     chat.html's `submitFeedback()` already calls), add one line alongside
     whatever it already does:

         from ..services.research import record_student_feedback
         record_student_feedback(conversation_id, message_index, rating)

     This mirrors the student's thumbs up/down onto the same answer_logs row
     a faculty reviewer might later rate correct/incorrect — see
     get_feedback_vs_accuracy_gap() in services/research.py, which actually
     measures the review's "distinguish perceived helpfulness from
     correctness" point instead of just asserting it.

  2. Wherever documents.py's upload route currently inserts extracted
     deadlines directly, switch to services/deadlines.py's insert_deadlines()
     instead of a raw INSERT, so every new deadline starts at status
     'detected' rather than bypassing the confirmation-state contract.

  3. In blueprints/calendar.py, add a small "Confirm" / "Edit & confirm" /
     "Dismiss" control per deadline that calls a new (student-facing, not
     admin) route wrapping services/deadlines.py's set_deadline_status() —
     that's the actual student-side half of the confirmation workflow; this
     file only reads the resulting stats.
"""
from flask import Blueprint, g, jsonify, render_template, request

from .. import config
from ..security import admin_page_required, admin_required
from ..services import research as research_service
from ..services.deadlines import get_deadline_confirmation_stats

bp = Blueprint("research", __name__)


@bp.route("/research")
@admin_page_required
def research_dashboard():
    return render_template(
        "research.html",
        s=g.student,
        admin_email=config.ADMIN_EMAIL,
        active="research",
        config_snapshot=research_service.get_config_snapshot(),
        answer_stats=research_service.get_answer_log_stats(),
        deadline_stats=get_deadline_confirmation_stats(),
        unrated_sample=research_service.get_unrated_sample(),
        feedback_gap=research_service.get_feedback_vs_accuracy_gap(),
    )


@bp.route("/research/rate-answer", methods=["POST"])
@admin_required
def rate_answer():
    """Records a reviewer's correct/incorrect/unsure judgment on one logged
    answer. rated_by is the reviewing admin's own email (from their
    session), not anything the client can spoof, so multiple reviewers
    rating overlapping samples stays attributable."""
    data = request.get_json(silent=True) or {}
    log_id = data.get("log_id")
    rating = data.get("rating")
    notes = data.get("notes", "")
    if not log_id or rating not in ("correct", "incorrect", "unsure"):
        return jsonify({"error": "log_id and a rating of correct/incorrect/unsure are required"}), 400
    updated = research_service.rate_answer(log_id, rating, notes, rated_by=g.student["email"])
    if not updated:
        return jsonify({"error": "Answer log not found"}), 404
    return jsonify({"ok": True})


@bp.route("/research/export.json")
@admin_required
def export_json():
    """Raw JSON export for offline analysis — inter-rater agreement,
    accuracy broken down by retrieval backend or course, error
    categorization. The review's accuracy benchmark ultimately needs a
    dataset to run statistics on, not just a dashboard to look at."""
    return jsonify({
        "config_snapshot": research_service.get_config_snapshot(),
        "answer_stats": research_service.get_answer_log_stats(),
        "deadline_stats": get_deadline_confirmation_stats(),
        "feedback_gap": research_service.get_feedback_vs_accuracy_gap(),
        "rated_answers": research_service.get_rated_sample(limit=1000),
    })
