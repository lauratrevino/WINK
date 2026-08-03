import os
import uuid

from flask import Blueprint, g, jsonify, render_template, request
from werkzeug.utils import secure_filename

from .. import config
from ..errors import log_error
from ..extensions import get_db
from ..security import login_required, page_login_required, admin_required, file_signature_valid, rate_limited, verified_required
from ..services.analytics import log_event
from ..services.deadlines import extract_deadlines, insert_deadlines
from ..services.documents import (
    extract_text, get_docs, get_global_docs, group_docs_by_course,
    invalidate_global_docs_cache, invalidate_student_docs_cache,
    store_document_chunks,
)

bp = Blueprint("documents", __name__)


@bp.route("/documents")
@page_login_required
def documents_page():
    try:
        s = g.student
        docs = get_docs(s["id"])
        grouped_docs = group_docs_by_course(docs)
        known_courses = sorted({(d.get("course") or "").strip() for d in docs
                                 if (d.get("course") or "").strip()})
        log_event(s["id"], "page_view", {"page": "documents"})
        return render_template("documents.html", s=s, admin_email=config.ADMIN_EMAIL, docs=docs,
                               grouped_docs=grouped_docs, known_courses=known_courses,
                               active="documents", max_docs=config.MAX_DOCS_PER_STUDENT)
    except Exception as e:
        log_error("documents.documents", e)
        return "<h2>Something went wrong</h2><p>Please try again, or <a href='/logout'>log out</a> and back in.</p>", 500


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

        # Temporary, this-conversation-only upload: extract the text and hand
        # it straight back to the client — never written to the documents
        # table, so it doesn't count against MAX_DOCS_PER_STUDENT and never
        # shows up in My Documents. The client resends this content with
        # each /chat call for the current conversation only; nothing here
        # persists once that conversation ends.
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
        # Optional: lets a student flag an upload as a past exam/quiz/study
        # guide rather than course material, so generate_practice_questions()
        # (see /generate-practice below) can use it as a style example
        # instead of a factual content source. Defaults to 'material' —
        # every existing upload flow is unaffected unless this is sent.
        doc_type = (request.form.get("doc_type") or "material").strip().lower()
        if doc_type not in config.DOC_TYPES:
            return jsonify({"error": "Invalid document type."}), 400

        replaced = False
        if config.DB_URL:
            # Document versioning: re-uploading the same filename for the same
            # course + CRN replaces the old copy instead of adding a new one —
            # this is almost always "the professor updated the syllabus," not
            # "a 21st document," and it keeps students from hitting the cap
            # just from re-uploading a corrected file.
            conn = get_db(); cur = conn.cursor()
            cur.execute("""SELECT id, filename FROM documents
                           WHERE student_id=%s AND lower(course)=lower(%s)
                           AND crn=%s AND lower(orig_name)=lower(%s)""",
                        (s["id"], course, crn, file.filename))
            existing = cur.fetchone()
            if existing:
                old_fp = os.path.join(config.UPLOAD_FOLDER, str(s["id"]), existing["filename"])
                if os.path.exists(old_fp):
                    try: os.remove(old_fp)
                    except Exception: pass
                cur.execute("DELETE FROM documents WHERE id=%s", (existing["id"],))
                conn.commit()
                replaced = True
            cur.close()

        if not replaced:
            existing_docs = get_docs(s["id"])
            if len(existing_docs) >= config.MAX_DOCS_PER_STUDENT:
                return jsonify({
                    "error": f"You've reached the {config.MAX_DOCS_PER_STUDENT}-document limit. "
                             f"Delete a document before uploading a new one."
                }), 400

        folder = os.path.join(config.UPLOAD_FOLDER, str(s["id"]))
        os.makedirs(folder, exist_ok=True)
        orig = file.filename
        saved = f"{uuid.uuid4().hex[:8]}_{secure_filename(orig)}"
        path = os.path.join(folder, saved)
        file.save(path)
        size = os.path.getsize(path)
        content = extract_text(path, orig)
        print(f"UPLOAD: {orig} → {len(content)} chars extracted")
        new_doc_id = None
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO documents
                           (student_id,filename,orig_name,course,crn,size_bytes,content,doc_type)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (s["id"], saved, orig, course, crn, size, content, doc_type))
            new_doc_id = cur.fetchone()["id"]
            conn.commit(); cur.close()
            # Chunked once here, at upload time, so /chat never has to
            # re-chunk on every question — see build_doc_context()'s
            # retrieval fallback in services/documents.py.
            store_document_chunks(new_doc_id, s["id"], s.get("university"), course, orig, content)

        # Deadline extraction: one small Haiku call per upload to pull out
        # assignment/exam dates so they can show up on the dashboard and in
        # reminder emails. Best-effort — never blocks the upload if it fails.
        deadlines_found = 0
        if new_doc_id and content:
            deadlines = extract_deadlines(content)
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
            cur.execute("SELECT filename FROM documents WHERE id=%s AND student_id=%s", (doc_id, s["id"]))
            doc = cur.fetchone()
            if doc:
                fp = os.path.join(config.UPLOAD_FOLDER, str(s["id"]), doc["filename"])
                if os.path.exists(fp): os.remove(fp)
                cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
                conn.commit()
                log_event(s["id"], "file_deleted", {"doc_id": doc_id})
            cur.close()
        invalidate_student_docs_cache(s["id"])
        return jsonify({"success": True, "docs": get_docs(s["id"])})
    except Exception as e:
        log_error("documents.delete", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


# ── General reference documents (admin-only) ────────────────
# These apply to every student's chat automatically (see build_global_doc_context
# and its use in /chat) but are stored with student_id=NULL, so they never show
# up in any student's own "My Documents" list or count against their 20-doc cap.
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
        if not university:
            return jsonify({"error": "Please choose which university this document applies to."}), 400
        if not file or not file.filename:
            return jsonify({"error": "No file selected"}), 400
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in config.ALLOWED_EXT:
            return jsonify({"error": f"File type .{ext} not allowed"}), 400
        if not file_signature_valid(file, ext):
            return jsonify({"error": f"This file doesn't look like a valid .{ext} file — it may be corrupted or mislabeled."}), 400

        folder = os.path.join(config.UPLOAD_FOLDER, "global")
        os.makedirs(folder, exist_ok=True)
        orig = file.filename
        saved = f"{uuid.uuid4().hex[:8]}_{secure_filename(orig)}"
        path = os.path.join(folder, saved)
        file.save(path)
        size = os.path.getsize(path)
        content = extract_text(path, orig)
        print(f"GLOBAL UPLOAD: {orig} ({university}) → {len(content)} chars extracted")
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO documents
                           (student_id,filename,orig_name,course,crn,size_bytes,content,university)
                           VALUES(NULL,%s,%s,%s,'',%s,%s,%s) RETURNING id""",
                        (saved, orig, label, size, content, university))
            new_doc_id = cur.fetchone()["id"]
            conn.commit(); cur.close()
            store_document_chunks(new_doc_id, None, university, label, orig, content)
        invalidate_global_docs_cache(university)
        log_event(s["id"], "global_file_uploaded", {"name": orig, "label": label, "university": university, "chars": len(content)})
        return jsonify({"success": True, "docs": get_global_docs(university), "chars_extracted": len(content)})
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
                invalidate_global_docs_cache(doc["university"])
                log_event(s["id"], "global_file_deleted", {"doc_id": doc_id})
            cur.close()
        return jsonify({"success": True, "docs": get_global_docs(university or None)})
    except Exception as e:
        log_error("documents.delete_global", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
