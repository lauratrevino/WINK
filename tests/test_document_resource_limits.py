"""
Real tests for the zip-bomb and PDF-page-count protections added to
extract_text() — using an actual pathological file (not a mock), and
confirming real legitimate files (the same ones used throughout this
project's other tests) still extract normally.
"""
import zipfile

import pytest


def _make_zip_bomb(path, entry_size_mb=50):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"A" * (entry_size_mb * 1024 * 1024))


def _make_entry_count_bomb(path, n_entries=6000):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(n_entries):
            zf.writestr(f"part{i}.xml", b"x")


class TestZipBombProtection:
    def test_high_compression_ratio_entry_is_rejected(self, tmp_path):
        from wink.services.documents import _zip_bomb_safe, extract_text
        bomb_path = tmp_path / "bomb.docx"
        _make_zip_bomb(str(bomb_path))
        assert _zip_bomb_safe(str(bomb_path)) is False
        assert extract_text(str(bomb_path), "bomb.docx") == ""

    def test_excessive_entry_count_is_rejected(self, tmp_path):
        from wink.services.documents import _zip_bomb_safe
        bomb_path = tmp_path / "manyparts.pptx"
        _make_entry_count_bomb(str(bomb_path))
        assert _zip_bomb_safe(str(bomb_path)) is False

    def test_real_uploaded_syllabus_still_passes(self):
        """The exact real file used throughout this project's other
        tests — confirms the new check doesn't false-positive on a
        genuine, ordinary document."""
        from wink.services.documents import _zip_bomb_safe, extract_text
        path = "/mnt/user-data/uploads/Spring2026Syllabus.docx"
        assert _zip_bomb_safe(path) is True
        text = extract_text(path, "Spring2026Syllabus.docx")
        assert len(text) > 1000, "the real syllabus should still extract normally"

    def test_not_a_real_zip_fails_closed(self, tmp_path):
        from wink.services.documents import _zip_bomb_safe
        fake = tmp_path / "notazip.docx"
        fake.write_bytes(b"this is not a zip file at all")
        assert _zip_bomb_safe(str(fake)) is False


class TestPdfPageLimit:
    def test_real_pdf_under_the_cap_still_extracts(self):
        from wink.services.documents import extract_text
        path = "/mnt/user-data/uploads/cs_2302_abet_syllabus.pdf"
        text = extract_text(path, "cs_2302_abet_syllabus.pdf")
        assert len(text) > 100, "the real 2-page PDF should still extract normally"

    def test_pdf_over_the_page_cap_is_skipped(self, monkeypatch, tmp_path):
        import wink.config as config
        from wink.services.documents import extract_text
        monkeypatch.setattr(config, "MAX_PDF_PAGES", 1)
        path = "/mnt/user-data/uploads/cs_2302_abet_syllabus.pdf"  # real 2-page PDF
        text = extract_text(path, "cs_2302_abet_syllabus.pdf")
        assert text == "", "a real PDF with more pages than the (lowered) cap should be skipped"
