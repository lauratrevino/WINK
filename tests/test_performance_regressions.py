"""Performance/resource-bounding regression tests.

These exist to give the issues fixed in the August 2026 engineering audit
concrete, measurable thresholds rather than leaving them as prose claims —
per that audit (issue #21): "the current test architecture does not
establish measurable thresholds" for retrieval latency, document
processing, or memory/candidate-set size. Each test here asserts a real
numeric bound, not just "it didn't crash."
"""
import io
import os
import time

from PIL import Image

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def register(client, email="perf@utep.edu"):
    from conftest import mark_email_verified
    resp = client.post("/register", data={
        "email": email, "password": "password123",
        "first_name": "Ada", "last_name": "Lovelace",
        "classification": "Senior", "major": "Computer Science", "university": "University of Texas at El Paso",
        "terms_agree": "on", "research_agree": "on",
    })
    mark_email_verified(email)
    return resp


class TestRetrievalCandidateBounding:
    """Covers audit issues #2/#4: get_student_chunks()/get_global_chunks()
    used to load EVERY chunk for a student (or university) into Python
    with no LIMIT. This directly inserts far more chunk rows than any
    realistic upload would produce and asserts the hard cap actually
    holds — the thing a manual smoke test with a normal-sized upload
    would never exercise."""

    def test_student_chunk_retrieval_never_exceeds_the_hard_cap(self, client, app):
        from wink.extensions import get_db
        from wink.services.documents import get_student_chunks
        import wink.config as config

        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", ("perf@utep.edu",))
            sid = cur.fetchone()["id"]
            cur.execute("INSERT INTO documents (student_id, filename, orig_name, course, size_bytes, content) "
                        "VALUES (%s, 'stress.docx', 'Stress.docx', 'CIS 3305', 1000, 'x') RETURNING id",
                        (sid,))
            doc_id = cur.fetchone()["id"]
            # Deliberately far beyond what a real upload (20-doc cap,
            # ~60,000-char extraction cap, ~1000-char chunks) would ever
            # produce for a single student, to prove the cap is a real
            # ceiling and not just coincidentally under whatever a normal
            # test fixture happens to generate.
            n_chunks = config.RETRIEVAL_MAX_CANDIDATE_CHUNKS * 3
            rows = [(doc_id, sid, "", i, f"deadline homework grading chunk number {i}", None)
                    for i in range(n_chunks)]
            from psycopg2.extras import execute_values
            execute_values(cur, "INSERT INTO document_chunks (document_id, student_id, university, "
                                 "chunk_index, content, embedding) VALUES %s", rows)
            conn.commit(); cur.close()

            result = get_student_chunks(sid, question="when is the homework deadline")
            assert len(result) <= config.RETRIEVAL_MAX_CANDIDATE_CHUNKS, (
                f"get_student_chunks returned {len(result)} rows for a student with "
                f"{n_chunks} chunks — the hard cap did not hold"
            )

            # The no-question fallback path is a separate code branch (a
            # plain LIMIT rather than the ts_rank pre-filter) and needs
            # its own assertion — a bug in one branch's LIMIT wouldn't be
            # caught by only exercising the other.
            result_no_q = get_student_chunks(sid, question=None)
            assert len(result_no_q) <= config.RETRIEVAL_MAX_CANDIDATE_CHUNKS

    def test_global_chunk_retrieval_never_exceeds_the_hard_cap(self, client, app):
        from wink.extensions import get_db
        from wink.services.documents import get_global_chunks
        import wink.config as config
        from psycopg2.extras import execute_values

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("INSERT INTO documents (student_id, filename, orig_name, course, university, "
                        "size_bytes, content) VALUES (NULL, 'globalref.docx', 'GlobalRef.docx', 'General', "
                        "'University of Texas at El Paso', 1000, 'x') RETURNING id")
            doc_id = cur.fetchone()["id"]
            n_chunks = config.RETRIEVAL_MAX_CANDIDATE_CHUNKS * 3
            rows = [(doc_id, None, "University of Texas at El Paso", i,
                     f"office hours policy chunk number {i}", None)
                    for i in range(n_chunks)]
            execute_values(cur, "INSERT INTO document_chunks (document_id, student_id, university, "
                                 "chunk_index, content, embedding) VALUES %s", rows)
            conn.commit(); cur.close()

            result = get_global_chunks("University of Texas at El Paso", question="what are office hours")
            assert len(result) <= config.RETRIEVAL_MAX_CANDIDATE_CHUNKS

    def test_keyword_prefilter_actually_prioritizes_relevant_chunks(self, client, app):
        """Not just that the cap holds, but that the ts_rank pre-filter
        does something useful — a needle-in-a-haystack chunk that matches
        the question should survive the LIMIT even when it's inserted
        after thousands of irrelevant chunks that would otherwise fill
        the cap first under a plain LIMIT-by-insertion-order query."""
        from wink.extensions import get_db
        from wink.services.documents import get_student_chunks
        import wink.config as config
        from psycopg2.extras import execute_values

        register(client, email="prefilter@utep.edu")
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", ("prefilter@utep.edu",))
            sid = cur.fetchone()["id"]
            cur.execute("INSERT INTO documents (student_id, filename, orig_name, course, size_bytes, content) "
                        "VALUES (%s, 'big.docx', 'Big.docx', 'CIS 3305', 1000, 'x') RETURNING id", (sid,))
            doc_id = cur.fetchone()["id"]

            filler_count = config.RETRIEVAL_MAX_CANDIDATE_CHUNKS * 2
            rows = [(doc_id, sid, "", i, "lecture notes about arrays and loops in chapter three", None)
                    for i in range(filler_count)]
            execute_values(cur, "INSERT INTO document_chunks (document_id, student_id, university, "
                                 "chunk_index, content, embedding) VALUES %s", rows)
            # The needle — inserted LAST, so a naive "first N by insertion
            # order" query would never surface it.
            cur.execute("INSERT INTO document_chunks (document_id, student_id, university, "
                        "chunk_index, content, embedding) VALUES (%s, %s, '', %s, %s, NULL)",
                        (doc_id, sid, filler_count, "the midterm exam retake policy requires written approval"))
            conn.commit(); cur.close()

            result = get_student_chunks(sid, question="what is the midterm exam retake policy")
            contents = [r["content"] for r in result]
            assert any("retake policy" in c for c in contents), (
                "the chunk matching the question's vocabulary should survive the candidate "
                "cap even when inserted after thousands of irrelevant chunks"
            )


class TestGlobalDocsLazyLoading:
    """Covers a finding from the second-pass audit (not in the original
    August 2026 report): get_global_docs() pulled full `content` for
    EVERY global reference document, with no LIMIT, on every single chat
    message across every student at a university — the same
    architectural problem as issues #2/#4 (unbounded chunk retrieval),
    one layer up at the document level, and unbounded by any admin-side
    document-count cap the way MAX_DOCS_PER_STUDENT bounds a student's
    own uploads. build_global_doc_context() now checks a cheap aggregate
    (get_global_docs_total_chars) before ever deciding whether to pay for
    the full fetch."""

    def _clear_global_docs(self, app):
        from wink.extensions import get_db
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("DELETE FROM documents WHERE student_id IS NULL")
            conn.commit(); cur.close()

    def test_total_chars_aggregate_matches_reality_without_loading_content(self, app):
        from wink.extensions import get_db
        from wink.services.documents import get_global_docs_total_chars
        import wink.config as config

        self._clear_global_docs(app)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            content = "office hours policy. " * 2000  # comfortably exceeds the global-context cap
            for i in range(5):
                cur.execute(
                    "INSERT INTO documents (student_id, filename, orig_name, course, university, "
                    "size_bytes, content) VALUES (NULL, %s, %s, 'General', "
                    "'University of Texas at El Paso', 1000, %s)",
                    (f"big{i}.docx", f"BigRef{i}.docx", content),
                )
            conn.commit(); cur.close()

            total_chars, n = get_global_docs_total_chars("University of Texas at El Paso")
            assert n == 5
            assert total_chars == len(content) * 5
            assert total_chars > config.MAX_GLOBAL_DOC_CONTEXT_CHARS, (
                "test setup should exceed the cap — otherwise this isn't exercising the "
                "retrieval branch this fix is actually about"
            )

    def test_global_doc_names_returns_names_without_content(self, app):
        from wink.extensions import get_db
        from wink.services.documents import get_global_doc_names

        self._clear_global_docs(app)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute(
                "INSERT INTO documents (student_id, filename, orig_name, course, university, "
                "size_bytes, content) VALUES (NULL, 'ref.docx', 'PolicyRef.docx', 'General', "
                "'University of Texas at El Paso', 1000, 'some content here')"
            )
            conn.commit(); cur.close()

            names = get_global_doc_names("University of Texas at El Paso")
            assert names == ["PolicyRef.docx"]

    def test_context_building_uses_retrieval_when_over_budget_and_stays_bounded(self, app):
        """The actual end-to-end proof: build a global reference pool far
        larger than the context cap, ask a specific question, and confirm
        the resulting context is bounded — not proportional to the full
        290,000+ characters actually stored."""
        from wink.extensions import get_db
        from wink.services.documents import build_global_doc_context
        import wink.config as config

        self._clear_global_docs(app)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            content = "office hours policy details for advising. " * 2000
            for i in range(5):
                cur.execute(
                    "INSERT INTO documents (student_id, filename, orig_name, course, university, "
                    "size_bytes, content) VALUES (NULL, %s, %s, 'General', "
                    "'University of Texas at El Paso', 1000, %s)",
                    (f"big{i}.docx", f"BigRef{i}.docx", content),
                )
            conn.commit(); cur.close()

            ctx = build_global_doc_context("University of Texas at El Paso",
                                            question="what is the office hours policy")
            assert len(ctx) < 5 * len(content), (
                "context should be bounded by retrieval, not proportional to the full stored content"
            )
            assert "office hours" in ctx.lower()

    def test_no_global_docs_returns_the_empty_message_without_erroring(self, app):
        from wink.services.documents import build_global_doc_context
        self._clear_global_docs(app)
        with app.app_context():
            ctx = build_global_doc_context("University of Texas at El Paso", question="anything")
        assert "No general reference documents" in ctx



    """Covers audit issue #1: nothing previously bounded the WORK done
    during extraction, only its eventual output size. Lowers the budget
    constant to something the test suite can actually hit in well under a
    second, rather than waiting out the real 45-second production value."""

    def test_pdf_extraction_stops_early_once_budget_exceeded(self, monkeypatch):
        from wink.services import documents as documents_module
        monkeypatch.setattr(documents_module, "_EXTRACTION_TIME_BUDGET_SECONDS", 0)
        path = os.path.join(FIXTURES_DIR, "sample_cs_syllabus.pdf")
        text = documents_module.extract_text(path, "cs_2302_abet_syllabus.pdf")
        assert "extraction stopped early" in text, (
            "a zero-second budget should trip on the very first page and leave a visible "
            "marker, rather than silently extracting the whole document anyway"
        )

    def test_docx_extraction_stops_early_once_budget_exceeded(self, monkeypatch):
        from wink.services import documents as documents_module
        monkeypatch.setattr(documents_module, "_EXTRACTION_TIME_BUDGET_SECONDS", 0)
        path = os.path.join(FIXTURES_DIR, "sample_syllabus.docx")
        text = documents_module.extract_text(path, "Spring2026Syllabus.docx")
        assert "extraction stopped early" in text

    def test_normal_extraction_is_unaffected_by_a_generous_budget(self):
        """The budget shouldn't cost anything for the common case — a real
        syllabus well under the budget should extract exactly as before."""
        from wink.services.documents import extract_text
        path = os.path.join(FIXTURES_DIR, "sample_syllabus.docx")
        text = extract_text(path, "Spring2026Syllabus.docx")
        assert "extraction stopped early" not in text
        assert len(text) > 1000


class TestOcrResourceBounding:
    """Covers audit issue #1's image-decompression-bomb angle: a small
    file on disk can decode into a bitmap large enough to make OCR
    (or just opening the image) slow — independent of the 16MB
    request-size cap, which bounds the file, not the decoded pixel
    buffer."""

    def test_oversized_image_skips_ocr_without_attempting_it(self, tmp_path, monkeypatch):
        from wink.services import documents as documents_module
        # Lower the cap rather than constructing an actual 40-megapixel
        # file (slow to generate and unnecessary — the code path under
        # test is the comparison itself, not PIL's own decoder).
        monkeypatch.setattr(documents_module, "_MAX_OCR_PIXELS", 100)
        img = Image.new("RGB", (50, 50), color="white")
        path = tmp_path / "oversized.png"
        img.save(path)

        def _fail_if_called(*a, **k):
            raise AssertionError("pytesseract.image_to_string should not be reached for an oversized image")
        if documents_module._OCR_AVAILABLE:
            monkeypatch.setattr(documents_module.pytesseract, "image_to_string", _fail_if_called)

        result = documents_module._extract_image_text(str(path), "oversized.png")
        assert "too large to run OCR on" in result

    def test_small_image_is_not_blocked_by_the_pixel_cap(self, tmp_path):
        from wink.services import documents as documents_module
        img = Image.new("RGB", (50, 50), color="white")
        path = tmp_path / "small.png"
        img.save(path)
        result = documents_module._extract_image_text(str(path), "small.png")
        assert "too large to run OCR on" not in result


class TestPreStreamContextParallelization:
    """Covers audit issue #3: the chat pipeline used to build the
    document, deadline, and global-reference contexts one after another.
    Also exercises issue #23's citation verification end to end, since
    both run inside the same request. The Anthropic client is faked out
    (rather than skipped) specifically so this test actually reaches and
    exercises the parallelized context-building block and the
    known_filenames assembly that feeds citation verification — a bare
    'the request didn't crash' assertion wouldn't prove any of that code
    actually ran, since a missing ANTHROPIC_API_KEY short-circuits before
    it in production.
    """

    class _FakeStream:
        def __init__(self, text):
            self._text = text

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            yield self._text

        def get_final_message(self):
            class _Usage:
                pass

            class _Msg:
                usage = _Usage()
                content = []

            return _Msg()

    class _FakeMessages:
        def __init__(self, text):
            self._text = text

        def stream(self, **kwargs):
            return TestPreStreamContextParallelization._FakeStream(self._text)

    class _FakeClient:
        def __init__(self, text):
            self.messages = TestPreStreamContextParallelization._FakeMessages(text)

    def _chat_with_fake_reply(self, client, app, reply_text, question, email):
        import wink.blueprints.chat as chat_module
        import wink.config as config

        register(client, email=email)
        with open(os.path.join(FIXTURES_DIR, "sample_syllabus.docx"), "rb") as f:
            client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")

        original_client = chat_module.anthropic_client
        original_key = config.ANTHROPIC_API_KEY
        chat_module.anthropic_client = self._FakeClient(reply_text)
        config.ANTHROPIC_API_KEY = "fake-key-for-test"
        try:
            resp = client.post("/chat", json={"messages": [{"role": "user", "content": question}]})
        finally:
            chat_module.anthropic_client = original_client
            config.ANTHROPIC_API_KEY = original_key
        return resp

    def test_chat_pipeline_completes_and_cites_a_real_uploaded_file(self, client, app):
        resp = self._chat_with_fake_reply(
            client, app,
            reply_text="Per Spring2026Syllabus.docx, the essay is due Friday.",
            question="when is the essay due", email="parallel1@utep.edu",
        )
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == "Per Spring2026Syllabus.docx, the essay is due Friday."

        with app.app_context():
            from wink.extensions import get_db
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT unverified_citations FROM answer_logs WHERE question=%s "
                        "ORDER BY id DESC LIMIT 1", ("when is the essay due",))
            row = cur.fetchone()
        assert row is not None, "the parallelized context-build/log pipeline should have logged this exchange"
        assert row["unverified_citations"] == "[]", (
            "citing a file that was actually uploaded should not be flagged"
        )

    def test_chat_pipeline_flags_a_hallucinated_filename(self, client, app):
        resp = self._chat_with_fake_reply(
            client, app,
            reply_text="Based on MidtermStudyGuide.pdf, focus on chapters 3-5.",
            question="what should I study", email="parallel2@utep.edu",
        )
        assert resp.status_code == 200
        resp.get_data()  # drains the streamed generator, which is what actually runs the DB write below — a response whose body was never read never finishes generate()

        with app.app_context():
            from wink.extensions import get_db
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT unverified_citations FROM answer_logs WHERE question=%s "
                        "ORDER BY id DESC LIMIT 1", ("what should I study",))
            row = cur.fetchone()
        assert row is not None
        import json as _json
        assert _json.loads(row["unverified_citations"]) == ["MidtermStudyGuide.pdf"], (
            "a filename never actually shown to the model should be flagged as unverified"
        )


class TestCitationVerification:
    """Covers audit issue #23: citation highlighting was a text match on
    the model's own output with no check that a named file was ever
    actually shown to the model. These are pure unit tests against the
    detection function itself — no DB or AI call needed."""

    def test_real_filename_is_not_flagged(self):
        from wink.blueprints.chat import _find_unverified_citations
        result = _find_unverified_citations(
            "According to Spring2026Syllabus.docx, the final is worth 30%.",
            known_filenames={"Spring2026Syllabus.docx", "Notes.pdf"},
        )
        assert result == []

    def test_invented_filename_is_flagged(self):
        from wink.blueprints.chat import _find_unverified_citations
        result = _find_unverified_citations(
            "Based on MidtermStudyGuide.pdf, focus on chapters 3-5.",
            known_filenames={"Spring2026Syllabus.docx"},
        )
        assert result == ["MidtermStudyGuide.pdf"]

    def test_filename_match_is_case_insensitive(self):
        from wink.blueprints.chat import _find_unverified_citations
        result = _find_unverified_citations(
            "See spring2026syllabus.DOCX for details.",
            known_filenames={"Spring2026Syllabus.docx"},
        )
        assert result == []

    def test_no_filenames_mentioned_returns_empty(self):
        from wink.blueprints.chat import _find_unverified_citations
        result = _find_unverified_citations(
            "The final exam is worth 30% of your grade.",
            known_filenames={"Spring2026Syllabus.docx"},
        )
        assert result == []
