import json

from .. import config
from ..errors import log_error
from ..extensions import get_db

_VALID_RATINGS = {"correct", "incorrect", "unsure"}


def log_answer(student_id, question, answer_text="", conversation_id=None, message_index=None,
                retrieval_backend="full_context", chunk_count=0,
                document_ids=None, latency_ms=None, prompt_version="v1", retrieved_context=""):
    if not config.DB_URL:
        return None
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""INSERT INTO answer_logs
                       (student_id, conversation_id, message_index, question, answer_text, model,
                        retrieval_backend, chunk_count, document_ids, latency_ms, prompt_version,
                        retrieved_context)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (student_id, conversation_id, message_index, question or "", answer_text or "",
                     config.CHAT_MODEL, retrieval_backend, chunk_count,
                     json.dumps(document_ids or []), latency_ms, prompt_version, retrieved_context or ""))
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id
    except Exception as e:
        log_error("services.research.log_answer", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def record_student_feedback(conversation_id, message_index, rating, student_id):
    if not config.DB_URL or rating not in ("up", "down"):
        return
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        # The ownership check is folded into the WHERE clause itself (a
        # subquery against conversations.student_id) rather than done as a
        # separate SELECT beforehand — that way there's no gap between
        # checking ownership and applying the update for another request to
        # exploit, and a student can never move another student's feedback
        # even if they know/guess a conversation_id and message_index.
        cur.execute("""UPDATE answer_logs SET student_feedback=%s
                       WHERE conversation_id=%s AND message_index=%s
                       AND conversation_id IN (
                           SELECT id FROM conversations WHERE student_id=%s
                       )""",
                    (rating, conversation_id, message_index, student_id))
        conn.commit()
    except Exception as e:
        log_error("services.research.record_student_feedback", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def rate_answer(log_id, rating, notes, rated_by):
    if not config.DB_URL or rating not in _VALID_RATINGS:
        return None
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""UPDATE answer_logs
                       SET faculty_rating=%s, faculty_notes=%s, rated_by=%s, rated_at=NOW()
                       WHERE id=%s RETURNING id""", (rating, notes or "", rated_by, log_id))
        updated = cur.fetchone()
        conn.commit()
        return dict(updated) if updated else None
    except Exception as e:
        log_error("services.research.rate_answer", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def get_feedback_vs_accuracy_gap():
    if not config.DB_URL:
        return None
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT student_feedback, faculty_rating, COUNT(*) as n
                       FROM answer_logs
                       WHERE student_feedback IS NOT NULL AND faculty_rating IS NOT NULL
                       GROUP BY student_feedback, faculty_rating""")
        rows = {(r["student_feedback"], r["faculty_rating"]): r["n"] for r in cur.fetchall()}
        if not rows:
            return None
        total = sum(rows.values())
        thumbs_up_but_incorrect = rows.get(("up", "incorrect"), 0)
        return {
            "total_with_both_ratings": total,
            "breakdown": {f"{fb}_{fr}": n for (fb, fr), n in rows.items()},
            "thumbs_up_but_incorrect": thumbs_up_but_incorrect,
            "thumbs_up_but_incorrect_pct": round(thumbs_up_but_incorrect / total * 100, 1) if total else None,
        }
    except Exception as e:
        log_error("services.research.get_feedback_vs_accuracy_gap", e)
        return None
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def get_config_snapshot():
    return {
        "chat_model": config.CHAT_MODEL,
        "embedding_model": config.EMBEDDING_MODEL,
        "neural_embeddings_configured": bool(config.VOYAGE_API_KEY),
        "retrieval_chunk_chars": config.RETRIEVAL_CHUNK_CHARS,
        "retrieval_chunk_overlap_chars": config.RETRIEVAL_CHUNK_OVERLAP_CHARS,
        "retrieval_top_n_student_docs": config.RETRIEVAL_TOP_N_STUDENT_DOCS,
        "retrieval_top_n_global_docs": config.RETRIEVAL_TOP_N_GLOBAL_DOCS,
        "max_doc_context_chars": config.MAX_DOC_CONTEXT_CHARS,
        "max_global_doc_context_chars": config.MAX_GLOBAL_DOC_CONTEXT_CHARS,
        "deadline_extraction_max_chars": config.DEADLINE_EXTRACTION_MAX_CHARS,
        "max_chat_history_messages": config.MAX_CHAT_HISTORY_MESSAGES,
    }


def get_answer_log_stats(days=30):
    if not config.DB_URL:
        return None
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT COUNT(*) as n, COUNT(DISTINCT student_id) as students,
                              AVG(latency_ms) as avg_latency, AVG(chunk_count) as avg_chunks
                       FROM answer_logs WHERE created_at >= NOW() - (%s * INTERVAL '1 day')""",
                    (days,))
        base = cur.fetchone()

        cur.execute("""SELECT retrieval_backend, COUNT(*) as n FROM answer_logs
                       WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                       GROUP BY retrieval_backend""", (days,))
        backend_breakdown = {r["retrieval_backend"]: r["n"] for r in cur.fetchall()}

        cur.execute("""SELECT faculty_rating, COUNT(*) as n FROM answer_logs
                       WHERE faculty_rating IS NOT NULL GROUP BY faculty_rating""")
        rating_breakdown = {r["faculty_rating"]: r["n"] for r in cur.fetchall()}

        correct, incorrect = rating_breakdown.get("correct", 0), rating_breakdown.get("incorrect", 0)
        judged = correct + incorrect  
        return {
            "window_days": days,
            "total_answers": base["n"] or 0,
            "unique_students": base["students"] or 0,
            "avg_latency_ms": round(base["avg_latency"], 0) if base["avg_latency"] else None,
            "avg_chunk_count": round(base["avg_chunks"], 1) if base["avg_chunks"] else None,
            "retrieval_backend_breakdown": backend_breakdown,
            "rating_breakdown": rating_breakdown,
            "rated_count": sum(rating_breakdown.values()),
            "accuracy_pct": round(correct / judged * 100, 1) if judged else None,
        }
    except Exception as e:
        log_error("services.research.get_answer_log_stats", e)
        return None
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def get_unrated_sample(limit=20):
    if not config.DB_URL:
        return []
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT id, student_id, question, answer_text, model, retrieval_backend,
                              chunk_count, document_ids, latency_ms, created_at
                       FROM answer_logs WHERE faculty_rating IS NULL
                       ORDER BY created_at DESC LIMIT %s""", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["document_ids"] = json.loads(r["document_ids"]) if r["document_ids"] else []
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        return rows
    except Exception as e:
        log_error("services.research.get_unrated_sample", e)
        return []
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def get_rated_sample(limit=500):
    if not config.DB_URL:
        return []
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT id, student_id, question, answer_text, model, retrieval_backend,
                              chunk_count, document_ids, latency_ms, prompt_version,
                              faculty_rating, faculty_notes, rated_by, rated_at, created_at
                       FROM answer_logs WHERE faculty_rating IS NOT NULL
                       ORDER BY created_at DESC LIMIT %s""", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["document_ids"] = json.loads(r["document_ids"]) if r["document_ids"] else []
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
            r["rated_at"] = r["rated_at"].isoformat() if r["rated_at"] else None
        return rows
    except Exception as e:
        log_error("services.research.get_rated_sample", e)
        return []
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def get_full_sample(limit=100000):
    """Every logged question/answer exchange, rated or not — for content
    analysis and research purposes that need the full corpus, not just the
    subset a faculty member has manually reviewed for accuracy. This is the
    only place the full answer text is stored outside a student's own live
    conversation history (see the comment on log_event("answer_given", ...)
    in blueprints/chat.py — a redundant third copy in the events table was
    removed; this table is the canonical research copy).

    limit defaults high rather than unbounded, as a safety ceiling against
    an unexpectedly large export rather than a real cap — a research pilot's
    full semester of data should comfortably fit under it.
    """
    if not config.DB_URL:
        return []
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT id, student_id, question, answer_text, model, retrieval_backend,
                              chunk_count, document_ids, latency_ms, prompt_version,
                              student_feedback, faculty_rating, faculty_notes, rated_by, rated_at,
                              created_at, retrieved_context
                       FROM answer_logs
                       ORDER BY created_at ASC LIMIT %s""", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["document_ids"] = json.loads(r["document_ids"]) if r["document_ids"] else []
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
            r["rated_at"] = r["rated_at"].isoformat() if r["rated_at"] else None
        return rows
    except Exception as e:
        log_error("services.research.get_full_sample", e)
        return []
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def get_export_history(limit=25):
    """Who exported the research corpus, when, and what — makes the
    audit trail actually reviewable, not just silently logged. Reads
    from the same events table log_event() (called at each export site
    in blueprints/research.py) already writes to."""
    if not config.DB_URL:
        return []
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT e.created_at, e.payload, s.email
                       FROM events e LEFT JOIN students s ON s.id = e.student_id
                       WHERE e.event_type = 'research_export'
                       ORDER BY e.created_at DESC LIMIT %s""", (limit,))
        rows = []
        for r in cur.fetchall():
            payload = json.loads(r["payload"] or "{}")
            rows.append({
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "exported_by": payload.get("exported_by") or r["email"] or "unknown",
                "export_type": payload.get("export_type", "unknown"),
                "row_count": payload.get("row_count"),
            })
        return rows
    except Exception as e:
        log_error("services.research.get_export_history", e)
        return []
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
