"""
Real end-to-end tests confirming the new UI elements actually wire up to
the backend correctly — using the exact field names the templates send,
not idealized test data.
"""
import io

import pytest


def register(client, email="student@utep.edu"):
    return client.post("/register", data={
        "email": email, "password": "password123",
        "first_name": "Ada", "last_name": "Lovelace",
        "classification": "Senior", "major": "Computer Science", "university": "UTEP",
    })


class TestNewUIWiring:
    def test_dashboard_renders_conflict_widget_markup(self, client):
        register(client)
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert b"conflict-card" in r.data
        assert b"loadDeadlineConflicts" in r.data

    def test_documents_page_has_doc_type_selector(self, client):
        register(client)
        r = client.get("/documents")
        assert r.status_code == 200
        assert b"doc-type-input" in r.data

    def test_upload_form_doc_type_field_persists_to_real_db(self, client, app):
        from wink.extensions import get_db
        register(client)
        resp = client.post("/upload", data={
            "file": (io.BytesIO(b"1. What is 2+2?"), "quiz.txt"),
            "course": "CS 2302", "crn": "111", "doc_type": "assessment",
        }, content_type="multipart/form-data")
        assert resp.status_code == 200

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT doc_type FROM documents WHERE course='CS 2302'")
            row = cur.fetchone()
            cur.close()
        assert row["doc_type"] == "assessment"

    def test_chat_page_has_feedback_buttons_wired(self, client):
        register(client)
        r = client.get("/chat-page")
        assert r.status_code == 200
        assert b"addFeedbackButtons" in r.data
        assert b"submitFeedback" in r.data

    def test_practice_page_reflects_real_uploaded_courses(self, client):
        register(client)
        client.post("/upload", data={
            "file": (io.BytesIO(b"syllabus content"), "syllabus.txt"),
            "course": "CS 2302", "crn": "111",
        }, content_type="multipart/form-data")
        r = client.get("/practice-page")
        assert r.status_code == 200
        assert b"CS 2302" in r.data

    def test_profile_update_with_language_reflects_on_dashboard(self, client):
        register(client)
        resp = client.post("/update-profile", json={
            "first_name": "Ada", "last_name": "Lovelace",
            "classification": "Senior", "major": "Computer Science",
            "university": "UTEP", "preferred_language": "Spanish",
        })
        assert resp.status_code == 200
        r = client.get("/dashboard")
        assert b"Spanish" in r.data

    def test_all_pages_still_render_after_ui_additions(self, client):
        """Broad regression guard: every page that got a nav-link addition
        must still return 200 and still contain the new practice link."""
        register(client)
        for path in ["/dashboard", "/documents", "/chat-page", "/calendar-page", "/practice-page"]:
            r = client.get(path)
            assert r.status_code == 200, f"{path} returned {r.status_code}"
            assert b"/practice-page" in r.data, f"{path} is missing the new nav link"
