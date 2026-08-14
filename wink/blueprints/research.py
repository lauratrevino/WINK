from flask import Blueprint, Response, g, jsonify, render_template, request

from .. import config
from ..errors import log_error
from ..security import admin_page_required, admin_required
from ..services import research as research_service
from ..services.analytics import log_event
from ..services.deadlines import get_deadline_confirmation_stats

bp = Blueprint("research", __name__)


@bp.route("/research")
@admin_page_required
def research_dashboard():
    try:
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
            export_history=research_service.get_export_history(),
        )
    except Exception as e:
        log_error("research.research_dashboard", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/research/rate-answer", methods=["POST"])
@admin_required
def rate_answer():
    try:
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
    except Exception as e:
        log_error("research.rate_answer", e, log_id=data.get("log_id") if isinstance(data, dict) else None)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/research/export.json")
@admin_required
def export_json():
    try:
        rated_answers = research_service.get_rated_sample(limit=1000)
        # Who exported what, when — the export itself is admin-protected
        # already, but that alone doesn't create a record of it having
        # happened. This is the same event log used throughout the rest
        # of the app, not a new mechanism.
        log_event(g.student["id"], "research_export", {
            "export_type": "rated_json", "row_count": len(rated_answers), "exported_by": g.student["email"],
        })
        return jsonify({
            "config_snapshot": research_service.get_config_snapshot(),
            "answer_stats": research_service.get_answer_log_stats(),
            "deadline_stats": get_deadline_confirmation_stats(),
            "feedback_gap": research_service.get_feedback_vs_accuracy_gap(),
            "rated_answers": rated_answers,
        })
    except Exception as e:
        log_error("research.export_json", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/research/export-full.json")
@admin_required
def export_full_json():
    """Every logged exchange, rated or not — for content analysis that
    needs the full corpus rather than just the faculty-reviewed subset
    export_json() above provides."""
    try:
        rows = research_service.get_full_sample()
        log_event(g.student["id"], "research_export", {
            "export_type": "full_json", "row_count": len(rows), "exported_by": g.student["email"],
        })
        return jsonify({"answers": rows})
    except Exception as e:
        log_error("research.export_full_json", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/research/export-full.csv")
@admin_required
def export_full_csv():
    """Same full corpus as export_full_json(), as CSV — the format most
    content-analysis tools (NVivo, Atlas.ti, Excel) actually want."""
    import csv
    import io
    try:
        rows = research_service.get_full_sample()
        log_event(g.student["id"], "research_export", {
            "export_type": "full_csv", "row_count": len(rows), "exported_by": g.student["email"],
        })
        buf = io.StringIO()
        fieldnames = ["id", "student_id", "created_at", "question", "answer_text", "model",
                      "retrieval_backend", "chunk_count", "document_ids", "latency_ms",
                      "prompt_version", "retrieved_context", "student_feedback", "faculty_rating",
                      "faculty_notes", "rated_by", "rated_at"]
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            r = dict(r)
            r["document_ids"] = ",".join(str(d) for d in (r.get("document_ids") or []))
            writer.writerow(r)
        resp = Response(buf.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = 'attachment; filename="wink_answers_full_export.csv"'
        return resp
    except Exception as e:
        log_error("research.export_full_csv", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
