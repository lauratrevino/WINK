"""
End-to-end tests for the retrieval feature (chunking + relevance ranking),
against real Postgres and the real uploaded syllabus files.
"""
import io

import pytest


def register(client, email="student@utep.edu"):
    """Registers, then marks the student verified — realistic for tests
    exercising upload/chat/practice functionality rather than the
    verification gate itself (see test_email_verification_gate.py)."""
    from conftest import mark_email_verified
    resp = client.post("/register", data={
        "email": email, "password": "password123",
        "first_name": "Ada", "last_name": "Lovelace",
        "classification": "Senior", "major": "Computer Science", "university": "UTEP",
    })
    mark_email_verified(email)
    return resp


class TestChunkStorageOnUpload:
    def test_upload_stores_real_chunks_in_postgres(self, client, app):
        from wink.extensions import get_db
        register(client)
        with open("/mnt/user-data/uploads/Spring2026Syllabus.docx", "rb") as f:
            resp = client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")
        assert resp.status_code == 200

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM document_chunks")
            n = cur.fetchone()["n"]
            cur.execute("SELECT content FROM document_chunks ORDER BY chunk_index LIMIT 1")
            first_chunk = cur.fetchone()["content"]
            cur.close()
        assert n > 5, f"expected multiple chunks for a 21k-char document, got {n}"
        assert "Spring2026Syllabus.docx" in first_chunk, "chunks should carry a provenance header"

    def test_delete_document_cascades_to_delete_its_chunks(self, client, app):
        from wink.extensions import get_db
        register(client)
        with open("/mnt/user-data/uploads/Spring2026Syllabus.docx", "rb") as f:
            client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()["id"]
            cur.close()

        client.post("/delete-file", json={"doc_id": doc_id})

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM document_chunks WHERE document_id=%s", (doc_id,))
            n = cur.fetchone()["n"]
            cur.close()
        assert n == 0, "deleting a document should cascade-delete its chunks"


class TestHybridContextBuilding:
    def test_small_upload_gets_full_content_no_truncation(self, client, app):
        """The core promise: when material comfortably fits the budget,
        every document is included in FULL — no chunking-based retrieval
        involved at all, exactly the behavior being protected here."""
        from wink.services.documents import build_doc_context, get_docs
        register(client)
        with open("/mnt/user-data/uploads/Spring2026Syllabus.docx", "rb") as f:
            content_bytes = f.read()
        client.post("/upload", data={
            "file": (io.BytesIO(content_bytes), "Spring2026Syllabus.docx"),
            "course": "CIS 3305", "crn": "12345",
        }, content_type="multipart/form-data")

        with app.app_context():
            from wink.security import current_student
            import wink.blueprints.chat  # noqa: ensures app context is set up the same way
            docs = get_docs(1)
            ctx = build_doc_context(docs, question="What is the late work policy?", sid=1)
        assert "[Shortened here to fit" not in ctx, "a single normal-sized syllabus must never be truncated"
        assert "Absolutely no late work" in ctx or "Late Work" in ctx, "full content should be present verbatim"

    def test_retrieval_kicks_in_only_once_budget_is_exceeded(self, app):
        """Directly exercises the hybrid logic's threshold with synthetic
        doc sizes, independent of the real upload pipeline."""
        from wink.services.documents import build_doc_context
        import wink.config as config

        small_docs = [{"orig_name": "a.txt", "course": "X", "content": "short content", "size_bytes": 100}]
        ctx_small = build_doc_context(small_docs)
        assert "[Shortened here to fit" not in ctx_small

        huge_content = "word " * (config.MAX_DOC_CONTEXT_CHARS + 5000)
        huge_docs = [{"orig_name": "big.txt", "course": "X", "content": huge_content, "size_bytes": len(huge_content)}]
        # No question/sid provided -> falls back to the old safety-net
        # truncation behavior rather than crashing.
        ctx_huge = build_doc_context(huge_docs)
        assert "[Shortened here to fit" in ctx_huge


class TestRetrievalRanking:
    def test_real_syllabus_late_work_question_finds_the_right_passage(self):
        from wink.services.documents import extract_text
        from wink.services.retrieval import chunk_text, rank_chunks

        text = extract_text("/mnt/user-data/uploads/Spring2026Syllabus.docx", "Spring2026Syllabus.docx")
        chunks = chunk_text(text, header="[Spring2026Syllabus.docx] (CIS 3305)")
        top = rank_chunks("What is the late work policy?", chunks, top_n=3)
        assert any("late work" in c.lower() for c in top)

    def test_academic_synonym_expansion_improves_or_matches_raw_ranking(self):
        """Regression guard for the query-expansion feature: verified
        during development (see README) that it helps in some cases and is
        neutral in others, never worse. This locks that in."""
        from wink.services.documents import extract_text
        from wink.services.retrieval import chunk_text, _expand_query
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        text = extract_text("/mnt/user-data/uploads/Spring2026Syllabus.docx", "Spring2026Syllabus.docx")
        chunks = chunk_text(text, header="[Spring2026Syllabus.docx] (CIS 3305)")

        def rank_position_of(marker, query):
            v = TfidfVectorizer(stop_words="english")
            m = v.fit_transform([query] + chunks)
            sims = cosine_similarity(m[0:1], m[1:]).flatten()
            order = sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)
            return next(i for i, idx in enumerate(order) if marker.lower() in chunks[idx].lower())

        q = "What textbook is required for this course?"
        pos_raw = rank_position_of("Required Text", q)
        pos_expanded = rank_position_of("Required Text", _expand_query(q))
        assert pos_expanded <= pos_raw, "synonym expansion should never rank the right answer worse"
