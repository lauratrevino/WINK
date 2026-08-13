import logging
import os
import uuid

from flask import Blueprint, abort, g, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import login_required, page_login_required, admin_required, file_signature_valid, rate_limited, verified_required
from ..services.analytics import log_event
from ..services.course_colors import ensure_course_colors, release_color_if_course_gone
from ..services.deadlines import extract_deadlines, insert_deadlines
from ..services.documents import (
    extract_text, get_docs, get_global_docs, group_docs_by_course,
    invalidate_global_docs_cache, invalidate_student_docs_cache,
    store_document_chunks,
)

bp = Blueprint("documents", __name__)
logger = logging.getLogger(__name__)


@bp.route("/course-colors")
@login_required
def course_colors():
    s = g.student
    docs = get_docs(s["id"])
    course_names = sorted({(d.get("course") or "").strip() for d in docs
                            if (d.get("course") or "").strip()}, key=str.lower)
    colors = ensure_course_colors(s["id"], course_names)
    return jsonify({"colors": colors, "courses": course_names})


@bp.route("/documents")
@page_login_required
def documents_page():
    try:
        s = g.student
        docs = get_docs(s["id"])
        grouped_docs = group_docs_by_course(docs)
        course_names = sorted({(d.get("course") or "").strip() for d in docs
                                if (d.get("course") or "").strip()}, key=str.lower)
        course_colors = ensure_course_colors(s["id"], course_names)
        log_event(s["id"], "page_view", {"page": "documents"})
        return render_template("documents.html", s=s, admin_email=config.ADMIN_EMAIL, docs=docs,
                               grouped_docs=grouped_docs, known_courses=course_names,
                               course_colors=course_colors,
                               active="documents", max_docs=config.MAX_DOCS_PER_STUDENT)
    except Exception as e:
        log_error("documents.documents", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/documents/<int:doc_id>/file")
@login_required
def download_file(doc_id):
    try:
        s = g.student
        if not config.DB_URL:
            abort(404)
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT filename, orig_name FROM documents WHERE id=%s AND student_id=%s", (doc_id, s["id"]))
        doc = cur.fetchone()
        cur.close()
        if not doc:
            abort(404)
        fp = os.path.join(config.UPLOAD_FOLDER, str(s["id"]), doc["filename"])
        if not os.path.exists(fp):
            log_error("documents.download_file",
                      Exception(f"Original file missing on disk for document {doc_id} "
                                f"(student {s['id']}) — extracted text still exists in the database, "
                                f"but the uploaded file itself is gone from storage. This usually means "
                                f"uploads aren't on persistent storage and were lost on a redeploy/restart."))
            return jsonify({"error": "The original file for this document is no longer available "
                                      "(it may have been lost during a server restart). WINK can still "
                                      "answer questions using the text that was already extracted from it — "
                                      "you may want to re-upload it to restore the downloadable original."}), 404
        return send_file(fp, as_attachment=False, download_name=doc["orig_name"])
    except HTTPException:
        raise
    except Exception as e:
        log_error("documents.download_file", e)
        abort(500)


@bp.route("/upload", methods=["POST"])
@login_required
@verified_required
def upload_file():
    try:
        s = g.student
        wait = rate_limited(f"upload:{s['id']}", max_calls=10, window_seconds=60)
        if wait:
            return jsonify({"error": "Too many uploads in a row — please wait a moment.", "retry_after": wait}), 429
        if "file" not in request.files:
            return jsonify({"error": "No file"}), 400
        file = request.files["file"]
        temporary = request.form.get("temporary", "").strip().lower() == "true"
        if not file or not file.filename:
            return jsonify({"error": "No file selected"}), 400
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in config.ALLOWED_EXT:
            return jsonify({"error": f"File type .{ext} not allowed"}), 400
        if not file_signature_valid(file, ext):
            return jsonify({"error": f"This file doesn't look like a valid .{ext} file — it may be corrupted or mislabeled."}), 400

        if temporary:
            tmp_name = f"{uuid.uuid4().hex[:8]}_{secure_filename(file.filename)}"
            tmp_path = os.path.join(config.UPLOAD_FOLDER, tmp_name)
            try:
                file.save(tmp_path)
                content = extract_text(tmp_path, file.filename)
            finally:
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except Exception: pass
            content = content[:config.MAX_TEMP_DOC_CHARS]
            log_event(s["id"], "temp_file_used", {"name": file.filename, "chars": len(content)})
            return jsonify({
                "success": True, "temporary": True,
                "name": file.filename, "content": content,
                "chars_extracted": len(content),
                "no_ocr_warning": ext in config.IMAGE_EXTS_NO_OCR
            })

        course = request.form.get("course", "").strip()[:100]
        crn = request.form.get("crn", "").strip()[:30]
        if not course:
            return jsonify({"error": "Please enter a course name."}), 400
        if not crn:
            return jsonify({"error": "Please enter a CRN#."}), 400
        doc_type = (request.form.get("doc_type") or "other").strip().lower()
        if doc_type not in config.DOC_TYPES:
            return jsonify({"error": "Invalid document type."}), 400

        existing = None
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""SELECT id, filename FROM documents
                           WHERE student_id=%s AND lower(course)=lower(%s)
                           AND crn=%s AND lower(orig_name)=lower(%s)""",
                        (s["id"], course, crn, file.filename))
            existing = cur.fetchone()
            if not existing:
                cur.execute("SELECT COUNT(*) as n FROM documents WHERE student_id=%s", (s["id"],))
                count = cur.fetchone()["n"]
                if count >= config.MAX_DOCS_PER_STUDENT:
                    cur.close()
                    return jsonify({
                        "error": f"You've reached the {config.MAX_DOCS_PER_STUDENT}-document limit. "
                                 f"Delete a document before uploading a new one."
                    }), 400
            cur.close()

        # Save and extract the NEW file first, under its own fresh filename —
        # only once that's fully succeeded (and inserted into the database
        # below) do we touch the old file/row. This way, a failure partway
        # through (a bad extraction, a DB error, a disk error) can never leave
        # a student with neither the old document nor the new one.
        folder = os.path.join(config.UPLOAD_FOLDER, str(s["id"]))
        os.makedirs(folder, exist_ok=True)
        orig = file.filename
        saved = f"{uuid.uuid4().hex[:8]}_{secure_filename(orig)}"
        path = os.path.join(folder, saved)
        file.save(path)
        size = os.path.getsize(path)
        content = extract_text(path, orig)
        logger.info("UPLOAD: %s → %d chars extracted", orig, len(content))
        new_doc_id = None
        replaced = False
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO documents
                           (student_id,filename,orig_name,course,crn,size_bytes,content,doc_type)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (s["id"], saved, orig, course, crn, size, content, doc_type))
            new_doc_id = cur.fetchone()["id"]
            if existing:
                # The new document is safely inserted — now it's safe to remove
                # the old one it's replacing.
                old_fp = os.path.join(config.UPLOAD_FOLDER, str(s["id"]), existing["filename"])
                if os.path.exists(old_fp):
                    try: os.remove(old_fp)
                    except Exception: pass
                cur.execute("DELETE FROM documents WHERE id=%s", (existing["id"],))
                replaced = True
            conn.commit(); cur.close()
            store_document_chunks(new_doc_id, s["id"], s.get("university"), course, orig, content)

        deadlines_found = 0
        if new_doc_id and content:
            deadlines = extract_deadlines(content, student_id=s["id"])
            if deadlines and config.DB_URL:
                insert_deadlines(s["id"], new_doc_id, course, deadlines)
                deadlines_found = len(deadlines)

        log_event(s["id"], "file_replaced" if replaced else "file_uploaded",
                  {"name": orig, "course": course, "crn": crn, "chars": len(content), "deadlines": deadlines_found})
        invalidate_student_docs_cache(s["id"])
        return jsonify({
            "success": True, "docs": get_docs(s["id"]), "chars_extracted": len(content),
            "replaced": replaced, "deadlines_found": deadlines_found,
            "no_ocr_warning": ext in config.IMAGE_EXTS_NO_OCR
        })
    except Exception as e:
        log_error("documents.upload", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/delete-file", methods=["POST"])
@login_required
def delete_file():
    try:
        s = g.student
        doc_id = (request.get_json() or {}).get("doc_id")
        if config.DB_URL and doc_id:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT filename, course FROM documents WHERE id=%s AND student_id=%s", (doc_id, s["id"]))
            doc = cur.fetchone()
            if doc:
                fp = os.path.join(config.UPLOAD_FOLDER, str(s["id"]), doc["filename"])
                if os.path.exists(fp): os.remove(fp)
                cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
                conn.commit()
                log_event(s["id"], "file_deleted", {"doc_id": doc_id})
                release_color_if_course_gone(s["id"], doc["course"])
            cur.close()
        invalidate_student_docs_cache(s["id"])
        return jsonify({"success": True, "docs": get_docs(s["id"])})
    except Exception as e:
        log_error("documents.delete", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/global-documents")
@admin_required
def list_global_documents():
    university = request.args.get("university", "").strip()
    return jsonify({"docs": get_global_docs(university or None)})


@bp.route("/upload-global", methods=["POST"])
@admin_required
def upload_global_document():
    try:
        s = g.student
        if "file" not in request.files:
            return jsonify({"error": "No file"}), 400
        file = request.files["file"]
        label = request.form.get("label", "").strip()[:100] or "General"
        university = request.form.get("university", "").strip()
        if university.strip().lower() == "all":
            university = "ALL"
        if not university:
            return jsonify({"error": "Please choose which university this document applies to, or select All Universities."}), 400
        if not file or not file.filename:
            return jsonify({"error": "No file selected"}), 400
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in config.ALLOWED_EXT:
            return jsonify({"error": f"File type .{ext} not allowed"}), 400
        if not file_signature_valid(file, ext):
            return jsonify({"error": f"This file doesn't look like a valid .{ext} file — it may be corrupted or mislabeled."}), 400

        folder = os.path.join(config.UPLOAD_FOLDER, "global")
        os.makedirs(folder, exist_ok=True)

        existing = None
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""SELECT id, filename FROM documents
                           WHERE student_id IS NULL AND lower(university)=lower(%s)
                           AND lower(orig_name)=lower(%s)""",
                        (university, file.filename))
            existing = cur.fetchone()
            cur.close()

        # Save and extract the NEW file first — only once it's successfully
        # saved, extracted, and inserted do we touch the old document/
        # deadlines it's replacing. Same reasoning as the per-student upload
        # fix: a failure partway through must never leave every student with
        # neither the old reference material nor the new.
        orig = file.filename
        saved = f"{uuid.uuid4().hex[:8]}_{secure_filename(orig)}"
        path = os.path.join(folder, saved)
        file.save(path)
        size = os.path.getsize(path)
        content = extract_text(path, orig)
        logger.info("GLOBAL UPLOAD: %s (%s) → %d chars extracted", orig, university, len(content))
        new_doc_id = None
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO documents
                           (student_id,filename,orig_name,course,crn,size_bytes,content,university)
                           VALUES(NULL,%s,%s,%s,'',%s,%s,%s) RETURNING id""",
                        (saved, orig, label, size, content, university))
            new_doc_id = cur.fetchone()["id"]
            if existing:
                # The new document is safely inserted — now it's safe to
                # remove the one it's replacing.
                cur.execute("DELETE FROM deadlines WHERE document_id=%s", (existing["id"],))
                old_fp = os.path.join(folder, existing["filename"])
                if os.path.exists(old_fp):
                    try: os.remove(old_fp)
                    except Exception: pass
                cur.execute("DELETE FROM documents WHERE id=%s", (existing["id"],))
            conn.commit(); cur.close()
            store_document_chunks(new_doc_id, None, university, label, orig, content)

        deadlines_found = 0
        if new_doc_id and content and config.DB_URL:
            deadlines = extract_deadlines(content, student_id=s["id"])
            if deadlines:
                conn = get_db(); cur = conn.cursor()
                # Only assign to students who can actually receive/see them —
                # a suspended or self-deleted account shouldn't accumulate
                # new deadlines from material uploaded after they left.
                if university == "ALL":
                    cur.execute("SELECT id FROM students WHERE is_active IS TRUE AND account_deleted_at IS NULL")
                else:
                    cur.execute("""SELECT id FROM students WHERE lower(university)=lower(%s)
                                   AND is_active IS TRUE AND account_deleted_at IS NULL""", (university,))
                student_ids = [r["id"] for r in cur.fetchall()]
                cur.close()
                for student_id in student_ids:
                    insert_deadlines(student_id, new_doc_id, label, deadlines)
                deadlines_found = len(deadlines)
                logger.info(
                    "GLOBAL UPLOAD: %s (%s) → %d deadline(s) applied to %d student(s)",
                    orig, university, len(deadlines), len(student_ids),
                )

        invalidate_global_docs_cache(None if university == "ALL" else university)
        log_event(s["id"], "global_file_uploaded",
                  {"name": orig, "label": label, "university": university,
                   "chars": len(content), "deadlines": deadlines_found})
        return jsonify({"success": True, "docs": get_global_docs(university),
                        "chars_extracted": len(content), "deadlines_found": deadlines_found})
    except Exception as e:
        log_error("documents.global_upload", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/delete-global-document", methods=["POST"])
@admin_required
def delete_global_document():
    try:
        s = g.student
        data = request.get_json() or {}
        doc_id = data.get("doc_id")
        university = (data.get("university") or "").strip()
        if config.DB_URL and doc_id:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT filename, university FROM documents WHERE id=%s AND student_id IS NULL", (doc_id,))
            doc = cur.fetchone()
            if doc:
                fp = os.path.join(config.UPLOAD_FOLDER, "global", doc["filename"])
                if os.path.exists(fp): os.remove(fp)
                cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
                conn.commit()
                invalidate_global_docs_cache(None if doc["university"] == "ALL" else doc["university"])
                log_event(s["id"], "global_file_deleted", {"doc_id": doc_id})
            cur.close()
        return jsonify({"success": True, "docs": get_global_docs(university or None)})
    except Exception as e:
        log_error("documents.delete_global", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
