"""
Tests for the neural embedding backend (Voyage AI). The real Voyage API
was never reachable from the sandbox this was built in (same network
restriction that blocked huggingface.co earlier) — these tests use a fake
client that mimics voyageai's real, installed response shape (confirmed
directly against the actual installed package: `.embed()` returns an
object with a plain `.embeddings` list-of-lists attribute — see the
inspection done during development). What's verified here: the storage/
retrieval plumbing, the ranking math, and the TF-IDF fallback when neural
embeddings aren't available. What's NOT verified: that a real call to
api.voyageai.com actually returns what we assume it does.
"""
import io
import math

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


class FakeEmbeddingsObject:
    """Mimics voyageai.object.embeddings.EmbeddingsObject's real shape —
    a plain `.embeddings` attribute holding a list of float lists."""
    def __init__(self, embeddings):
        self.embeddings = embeddings
        self.total_tokens = sum(len(e) for e in embeddings)


def _unit_vector_for(text):
    """Deterministic, cheap stand-in for a real embedding: hashes the text
    into a small fixed-size unit vector. Two different texts get
    (almost certainly) different vectors; the same text always gets the
    same one — good enough to test ranking/storage logic without a real
    model."""
    import hashlib
    h = hashlib.sha256(text.encode()).digest()
    vec = [b / 255.0 for b in h[:16]]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeVoyageClient:
    """Fake voyageai.Client — `.embed()` matches the real installed
    package's signature and return shape exactly."""
    def __init__(self):
        self.calls = []

    def embed(self, texts, model=None, input_type=None, **kwargs):
        self.calls.append({"texts": list(texts), "model": model, "input_type": input_type})
        return FakeEmbeddingsObject([_unit_vector_for(t) for t in texts])


class TestNeuralRankingMath:
    """Pure-function tests for the ranking logic itself, independent of
    the DB or the fake client — hand-constructed vectors with a known
    correct answer."""

    def test_ranks_by_cosine_similarity_of_precomputed_embeddings(self, monkeypatch):
        import wink.services.retrieval as retrieval

        # Query vector points mostly along dimension 0.
        query_vec = [1.0, 0.0, 0.0]

        class FakeClientForRanking:
            def embed(self, texts, model=None, input_type=None, **kwargs):
                return FakeEmbeddingsObject([query_vec])

        monkeypatch.setattr(retrieval, "voyage_client", FakeClientForRanking())

        chunks = ["chunk about topic A", "chunk about topic B", "chunk about topic C"]
        # chunk 1 is closest to the query vector, chunk 2 is exactly
        # orthogonal (similarity 0), chunk 0 points the opposite way.
        chunk_embeddings = [[-1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.0, 1.0, 0.0]]

        result = retrieval.rank_chunks("some question", chunks, top_n=2, chunk_embeddings=chunk_embeddings)
        assert result[0] == "chunk about topic B", "the most-aligned chunk must rank first"
        assert len(result) == 2

    def test_falls_back_to_tfidf_if_any_embedding_missing(self, monkeypatch):
        import wink.services.retrieval as retrieval
        monkeypatch.setattr(retrieval, "voyage_client", FakeVoyageClient())

        chunks = ["a", "b", "c", "d"]
        chunk_embeddings = [[1.0], [1.0], None, [1.0]]  # one missing
        # Should not raise, and should still return top_n chunks via TF-IDF fallback.
        result = retrieval.rank_chunks("query text", chunks, top_n=2, chunk_embeddings=chunk_embeddings)
        assert len(result) == 2

    def test_falls_back_to_tfidf_if_no_client_configured(self):
        import wink.services.retrieval as retrieval
        assert retrieval.voyage_client is None  # true baseline state in this test env
        chunks = ["late work policy is strict", "office hours are Tuesday", "grading is by rubric"]
        result = retrieval.rank_chunks("what is the late work policy", chunks, top_n=1)
        assert result == ["late work policy is strict"]


class TestEmbeddingStorageRealDB:
    def test_upload_stores_real_embeddings_when_backend_configured(self, client, app, monkeypatch):
        import wink.services.documents as documents_service
        import wink.services.retrieval as retrieval
        from wink.extensions import get_db

        fake = FakeVoyageClient()
        monkeypatch.setattr(retrieval, "voyage_client", fake)

        register(client)
        with open("/mnt/user-data/uploads/Spring2026Syllabus.docx", "rb") as f:
            resp = client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")
        assert resp.status_code == 200

        # Confirm the fake client was actually called with document-type chunks
        assert any(c["input_type"] == "document" for c in fake.calls)

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM document_chunks WHERE embedding IS NOT NULL")
            n_with_embeddings = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM document_chunks")
            n_total = cur.fetchone()["n"]
            cur.close()
        assert n_with_embeddings > 0
        assert n_with_embeddings == n_total, "every chunk should have gotten a real stored embedding"

    def test_upload_without_backend_leaves_embeddings_null(self, client, app):
        """Baseline: with no Voyage client configured (this test's default
        env), chunks are still stored, just without embeddings — TF-IDF
        still works from content alone."""
        from wink.extensions import get_db

        register(client)
        with open("/mnt/user-data/uploads/Spring2026Syllabus.docx", "rb") as f:
            client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM document_chunks WHERE embedding IS NOT NULL")
            n_with_embeddings = cur.fetchone()["n"]
            cur.close()
        assert n_with_embeddings == 0

    def test_chat_context_uses_neural_ranking_when_available(self, client, app, monkeypatch):
        """End-to-end through build_doc_context(): with a configured fake
        Voyage client and a large-enough document set to trigger retrieval,
        the neural path (not TF-IDF) should be the one actually used."""
        import wink.services.documents as documents_service
        import wink.services.retrieval as retrieval
        import wink.config as config

        fake = FakeVoyageClient()
        monkeypatch.setattr(retrieval, "voyage_client", fake)

        register(client)
        huge_content = "Course policy detail. " * 5000  # force over the budget
        with open("/mnt/user-data/uploads/Spring2026Syllabus.docx", "rb") as f:
            client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")
        client.post("/upload", data={
            "file": (io.BytesIO(huge_content.encode()), "huge_notes.txt"),
            "course": "CIS 3305", "crn": "12345",
        }, content_type="multipart/form-data")

        fake.calls.clear()  # only care about calls made during context building, not upload
        with app.app_context():
            docs = documents_service.get_docs(1)
            documents_service.build_doc_context(docs, question="What is the late work policy?", sid=1)

        assert any(c["input_type"] == "query" for c in fake.calls), \
            "a query embedding call should have been made — neural ranking path was used, not TF-IDF"
