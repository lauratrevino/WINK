"""
Research-facing metrics for WINK: per-answer provenance logging, the
reproducibility "config snapshot" (model/retrieval/chunking settings a
later analysis would need to know to interpret results), and a lightweight
interface for a faculty reviewer to score a sample of real answers for
correctness.

This module exists directly in response to the July 2026 external review of
WINK, which found the system had a good architecture for answering
accurately but no independently demonstrated accuracy rate, no passage-level
citation trail, and no separation between "students approved of this
answer" and "this answer was actually correct." log_answer()/rate_answer()
below are the minimum viable version of that: every answer gets a row
recording exactly what produced it, and a reviewer (not the student, not the
model) can later mark a sample of those rows correct/incorrect/unsure.

None of this changes student-facing behavior. Call log_answer() from the
/chat route right after a response is generated (see the integration note
in blueprints/research.py) — everything else here only reads what's been
logged.

CONNECTION HYGIENE (fixed Aug 2026): every function below now closes its
cursor/connection in a `finally` block. Previously, a failed cur.execute()
(e.g. because the answer_logs table didn't exist yet) would jump straight
to `except` and return None cleanly, but the connection itself was never
returned to the pool. Since log_answer() runs on every /chat response, that
leaked one connection per chat message whenever the table was missing or
any query failed — enough failed calls exhausts the pool, and everything
else waiting on get_db() (including unrelated routes) starts hanging until
Render's proxy times out and serves a 502. The `finally: cur.close()` /
`conn` handling below is what actually matters here — same
try/except/return-None-on-failure behavior as before, just with a
guaranteed close.
"""
import json

from .. import config
from ..errors import log_error
from ..extensions import get_db

_VALID_RATINGS = {"correct", "incorrect", "unsure"}


def ensure_answer_logs_table():
    """Creates the answer_logs table if it doesn't exist yet. Safe to call
    more than once (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS
    equivalents aren't needed here since this is one CREATE statement with
    every column already in it). Called from the one-time
    /research/run-migration-answer-logs route — see blueprints/research.py.
    This is the missing piece that was causing every log_answer() call to
    fail (and leak a connection, before the fix above) on any deploy where
    this table was never created."""
    if not config.DB_URL:
        return False
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS answer_logs (
                id SERIAL PRIMARY KEY,
                student_id INTEGER,
                conversation_id INTEGER,
                message_index INTEGER,
                question TEXT,
                answer_text TEXT,
                model TEXT,
                retrieval_backend TEXT,
                chunk_count INTEGER DEFAULT 0,
                document_ids TEXT,
                latency_ms INTEGER,
                prompt_version TEXT,
                student_feedback TEXT,
                faculty_rating TEXT,
                faculty_notes TEXT,
                rated_by TEXT,
                rated_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        conn.commit()
        return True
    except Exception as e:
        log_error("services.research.ensure_answer_logs_table", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass


def log_answer(student_id, question, answer_text="", conversation_id=None, message_index=None,
                retrieval_backend="full_context", chunk_count=0,
                document_ids=None, latency_ms=None, prompt_version="v1"):
    """Records one row of provenance for one chat answer. Call this once per
    /chat response, right after the model call completes — model/
    retrieval_backend/chunk_count/document_ids should describe exactly what
    fed that specific answer (e.g. "neural" + the chunk indices actually
    returned by rank_chunks(), not just "retrieval was available").
    message_index should match the index used by the existing /rate-answer
    thumbs up/down route (conversation.messages is a list; this is that
    answer's position in it) — that's what lets record_student_feedback()
    below find the right row later. Silently no-ops (returns None) if
    there's no database or the insert fails — logging failures should never
    break a chat response for the student. Truncates question/answer_text
    defensively since this is diagnostic data, not the source of truth for
    conversation history (that's `conversations.messages`)."""
    if not config.DB_URL:
        return None
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""INSERT INTO answer_logs
                       (student_id, conversation_id, message_index, question, answer_text, model,
                        retrieval_backend, chunk_count, document_ids, latency_ms, prompt_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (student_id, conversation_id, message_index, (question or "")[:2000], (answer_text or "")[:4000],
                     config.CHAT_MODEL, retrieval_backend, chunk_count,
                     json.dumps(document_ids or []), latency_ms, prompt_version))
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


def record_student_feedback(conversation_id, message_index, rating):
    """Mirrors a thumbs up/down from the existing /rate-answer route onto
    the matching answer_logs row (matched by conversation_id +
    message_index), so satisfaction and correctness end up on the same row
    without ever overwriting each other. Call this from /rate-answer
    alongside whatever it already does — it doesn't replace that route's
    existing storage, just adds the correlation. No-ops quietly if no
    matching row exists (e.g. logging wasn't wired in yet when that answer
    was generated) — a missed correlation, not an error worth surfacing to
    the student."""
    if not config.DB_URL or rating not in ("up", "down"):
        return
    conn = None
    cur = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""UPDATE answer_logs SET student_feedback=%s
                       WHERE conversation_id=%s AND message_index=%s""",
                    (rating, conversation_id, message_index))
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
    """A faculty/admin reviewer's correctness judgment on one logged answer
    — the actual accuracy signal the review found missing (thumbs up/down
    from students measures satisfaction, not correctness). Ownership isn't
    checked here since this is admin-only (see admin_required in
    blueprints/research.py); rated_by is stored so multiple reviewers rating
    the same sample can later be compared for inter-rater agreement."""
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
    """Cross-tabulates student thumbs up/down against faculty correctness
    ratings, for every answer that has BOTH — directly the comparison the
    WINK review asked for ("research reports must distinguish perceived
    helpfulness from faculty-rated correctness"), not just each in
    isolation. The number to watch is thumbs_up_but_incorrect: a confident,
    well-formatted wrong answer a student approved of anyway. Returns None
    if nothing has both kinds of rating yet."""
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
    """The retrieval/model/chunking settings in effect right now — no DB
    needed, this is just config.py's current values, gathered in one place.
    Store or screenshot this alongside any accuracy numbers you report:
    without it, a later reader can't tell whether a reported error rate was
    measured with TF-IDF or neural retrieval, or what MAX_DOC_CONTEXT_CHARS
    was at the time."""
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
    """Aggregate provenance stats for the last `days` days: volume, which
    retrieval backend actually served each answer, average latency/chunk
    count, and — the headline number — accuracy among whatever a reviewer
    has actually rated so far. accuracy_pct is None (not 0%) until at least
    one answer has been rated, so an empty research page can't be
    misread as "0% accurate"."""
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
        judged = correct + incorrect  # excludes 'unsure' from the accuracy denominator
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
    """The most recent answers nobody has rated yet — what a reviewer sees
    to work through on the research page. Deliberately recent-first rather
    than random: for an early pilot, "did the last 20 real answers hold up"
    is more actionable than a statistically ideal random sample would be at
    this volume."""
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
    """Every rated answer (up to `limit`, most recent first) — the actual
    dataset behind accuracy_pct above, for offline analysis: inter-rater
    agreement (once more than one rated_by shows up), error categorization,
    accuracy broken down by retrieval_backend, etc. Exposed via
    /research/export.json."""
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
