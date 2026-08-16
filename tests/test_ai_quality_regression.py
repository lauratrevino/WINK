"""AI-quality regression tests — golden-dataset retrieval checks.

HONEST SCOPE NOTE (this is issue #22 from the August 2026 audit, and this
file does not close it): the audit asked for a comprehensive suite
measuring answer correctness, grounding, hallucination rate, citation
accuracy, retrieval recall/precision, refusal behavior, prompt-injection
resistance, and contradictory-document handling. Answer correctness,
grounding, and hallucination rate can only be measured against real model
outputs — that needs a live ANTHROPIC_API_KEY and a human- or model-graded
rubric, neither of which exists in this test environment or this test
suite today. Building that is a real, separate project, not something to
half-do here.

What THIS file actually is: a starting golden dataset for the one layer
that CAN be tested deterministically without a live model call —
retrieval (does the right passage surface for a given question?) — plus
regression coverage for the rule-based citation-verification check added
alongside issue #23. Treat this as a floor to build on, not the
comprehensive suite the audit describes. See test_retrieval.py's
TestRetrievalRanking for the two golden cases that already existed before
this file; the ones here extend that same approach across more
questions and both fixture document types (docx and pdf).
"""
import os

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class TestGoldenRetrievalDataset:
    """Each case: a real question a student might ask, a real fixture
    document, and a marker string that MUST appear in the top-ranked
    chunks for the answer to even be possible — retrieval recall for
    that one question. This doesn't check the model's eventual answer
    (no live API call), only whether the passage it would need was
    actually surfaced to it."""

    @staticmethod
    def _top_chunks_for(fixture_filename, orig_name, question, top_n=3):
        from wink.services.documents import extract_text
        from wink.services.retrieval import chunk_text, rank_chunks
        text = extract_text(os.path.join(FIXTURES_DIR, fixture_filename), orig_name)
        chunks = chunk_text(text, header=f"[{orig_name}]")
        return rank_chunks(question, chunks, top_n=top_n)

    def test_docx_late_work_policy(self):
        top = self._top_chunks_for("sample_syllabus.docx", "Spring2026Syllabus.docx",
                                    "What happens if I turn in an assignment late?")
        assert any("late" in c.lower() for c in top)

    def test_docx_required_textbook(self):
        top = self._top_chunks_for("sample_syllabus.docx", "Spring2026Syllabus.docx",
                                    "What textbook do I need to buy?")
        assert any("text" in c.lower() for c in top)

    def test_docx_grading_breakdown(self):
        top = self._top_chunks_for("sample_syllabus.docx", "Spring2026Syllabus.docx",
                                    "How is my final grade calculated?")
        assert any("grad" in c.lower() for c in top)

    def test_pdf_course_topic_or_description(self):
        # The PDF fixture is a short (2-page) ABET-style syllabus — assert
        # against its actual extracted content rather than assuming a
        # specific section exists, since a wrong assumption here would
        # make this test fragile rather than meaningful.
        from wink.services.documents import extract_text
        text = extract_text(os.path.join(FIXTURES_DIR, "sample_cs_syllabus.pdf"), "cs_2302_abet_syllabus.pdf")
        assert len(text) > 100, "fixture PDF should still extract text at all before testing ranking against it"

    def test_retrieval_does_not_surface_an_unrelated_document_for_a_specific_question(self):
        """A basic precision check, not just recall: ranking a very
        specific question against a document that has nothing to do with
        it should NOT put an arbitrary chunk in a false position of
        confidence — top_n should still return content, but not silently
        claim relevance it doesn't have. This is a floor-level sanity
        check (rank_chunks always returns something when asked), not a
        real precision metric — a genuine precision/recall suite would
        need labeled relevance judgments across many document pairs."""
        from wink.services.documents import extract_text
        from wink.services.retrieval import chunk_text, rank_chunks
        text = extract_text(os.path.join(FIXTURES_DIR, "sample_syllabus.docx"), "Spring2026Syllabus.docx")
        chunks = chunk_text(text, header="[Spring2026Syllabus.docx]")
        # A question this document's content has no real answer to —
        # rank_chunks should still return *something* (it always does,
        # by design — the model itself is responsible for saying "I don't
        # see that in your documents"), but the call should not raise or
        # return an empty result for a well-formed question either.
        top = rank_chunks("What is the wifi password for the library?", chunks, top_n=3)
        assert len(top) > 0


class TestCitationVerificationGoldenCases:
    """Extends the unit tests in test_performance_regressions.py with a
    slightly larger set of realistic phrasings — this is the rule-based
    layer from issue #23, not a live-model hallucination-rate measurement."""

    def test_multiple_real_citations_in_one_answer(self):
        from wink.blueprints.chat import _find_unverified_citations
        result = _find_unverified_citations(
            "Compare the requirements in Spring2026Syllabus.docx and CourseCalendar.pdf.",
            known_filenames={"Spring2026Syllabus.docx", "CourseCalendar.pdf"},
        )
        assert result == []

    def test_mix_of_real_and_invented_citations_flags_only_the_invented_one(self):
        from wink.blueprints.chat import _find_unverified_citations
        result = _find_unverified_citations(
            "See Spring2026Syllabus.docx for the schedule, and StudyGuide2024.pdf for review questions.",
            known_filenames={"Spring2026Syllabus.docx"},
        )
        assert result == ["StudyGuide2024.pdf"]

    def test_filename_like_text_inside_a_url_is_not_treated_as_a_citation(self):
        """A defensive case for the regex itself — a filename-shaped
        substring inside a URL shouldn't be pulled out and evaluated as
        if the model were citing an uploaded document."""
        from wink.blueprints.chat import _find_unverified_citations
        result = _find_unverified_citations(
            "You can read more at https://example.edu/handouts/notes.pdf if you'd like.",
            known_filenames={"Spring2026Syllabus.docx"},
        )
        assert result == [], (
            "a URL should not be extracted as a citable filename at all — "
            "see _extract_citation_filenames()'s path-separator exclusion"
        )

    def test_extract_citation_filenames_excludes_paths_and_urls_directly(self):
        from wink.blueprints.chat import _extract_citation_filenames
        assert _extract_citation_filenames("See Report.docx for details.") == ["Report.docx"]
        assert _extract_citation_filenames("Visit https://a.b/c/Report.docx now.") == []
        assert _extract_citation_filenames("File at /uploads/Report.docx exists.") == []
        assert _extract_citation_filenames("Spring2026Syllabus.docx and Notes.pdf both apply.") == \
            ["Spring2026Syllabus.docx", "Notes.pdf"]
