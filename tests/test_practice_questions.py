import io
import os

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
import json



def register(client, email="student@utep.edu"):
    from conftest import mark_email_verified
    resp = client.post("/register", data={
        "email": email, "password": "password123",
        "first_name": "Ada", "last_name": "Lovelace",
        "classification": "Senior", "major": "Computer Science", "university": "University of Texas at El Paso",
        "terms_agree": "on", "research_agree": "on",
    })
    mark_email_verified(email)
    return resp


class FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeTextBlock(text)]
        self.usage = None  # log_token_usage() already handles a falsy usage gracefully


class FakeMessages:
    def __init__(self, response_text):
        self.response_text = response_text
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return FakeResponse(self.response_text)


class FakeClient:
    def __init__(self, response_text):
        self.messages = FakeMessages(response_text)


REALISTIC_QUESTIONS_JSON = json.dumps([
    {"question": "What is the late work policy in this course?",
     "answer": "No late work is accepted.",
     "explanation": "The syllabus states absolutely no late work is accepted."},
    {"question": "How many mental-health absences are permitted?",
     "answer": "Two.",
     "explanation": "The syllabus allows a total of two class absences for mental health."},
])


class TestGeneratePractice:
    def test_generates_real_questions_from_real_uploaded_syllabus(self, client, monkeypatch):
        import wink.blueprints.chat as chat_bp
        import wink.services.practice as practice_service
        import wink.config as config

        register(client)
        with open(os.path.join(FIXTURES_DIR, "sample_syllabus.docx"), "rb") as f:
            resp = client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")
        assert resp.status_code == 200

        fake = FakeClient(REALISTIC_QUESTIONS_JSON)
        monkeypatch.setattr(practice_service, "anthropic_client", fake)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)

        resp = client.post("/generate-practice", json={"course": "CIS 3305", "count": 2})
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert len(body["questions"]) == 2
        assert body["questions"][0]["question"] == "What is the late work policy in this course?"
        assert body["based_on_assessment_style"] is False

        sent = fake.messages.last_call
        assert "Absolutely no late work" in sent["messages"][0]["content"] or "Late Work" in sent["messages"][0]["content"]

    def test_assessment_tagged_upload_used_as_style_reference_only(self, client, monkeypatch):
        import wink.blueprints.chat as chat_bp
        import wink.services.practice as practice_service
        import wink.config as config

        register(client)
        with open(os.path.join(FIXTURES_DIR, "sample_syllabus.docx"), "rb") as f:
            client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")
        client.post("/upload", data={
            "file": (io.BytesIO(b"1. What is 2+2? A) 3 B) 4 C) 5\n2. Define recursion."), "old_quiz.txt"),
            "course": "CIS 3305", "crn": "12345", "doc_type": "assessment",
        }, content_type="multipart/form-data")

        fake = FakeClient(REALISTIC_QUESTIONS_JSON)
        monkeypatch.setattr(practice_service, "anthropic_client", fake)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)

        resp = client.post("/generate-practice", json={"course": "CIS 3305"})
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["based_on_assessment_style"] is True

        sent = fake.messages.last_call
        assert "recursion" in sent["messages"][0]["content"].lower()
        assert "Do NOT reuse, reword, or lightly disguise" in sent["system"]

    def test_rejects_invalid_doc_type_on_upload(self, client):
        register(client)
        resp = client.post("/upload", data={
            "file": (io.BytesIO(b"content"), "file.txt"),
            "course": "CIS 3305", "crn": "12345", "doc_type": "not-a-real-type",
        }, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_no_material_for_course_returns_helpful_error(self, client, monkeypatch):
        import wink.blueprints.chat as chat_bp
        import wink.config as config
        register(client)
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        resp = client.post("/generate-practice", json={"course": "A Course With No Uploads"})
        assert resp.status_code == 400
        assert "No material found" in resp.get_json()["error"]


class TestTemporaryHandoutPractice:

    def test_generates_questions_from_a_temporary_handout_alone(self, client, monkeypatch, app):
        import wink.blueprints.chat as chat_bp
        import wink.services.practice as practice_service
        import wink.config as config
        from wink.extensions import get_db

        register(client)

        handout_text = (
            b"Chapter 7 Handout: Binary Search Trees\n\n"
            b"A binary search tree (BST) is a binary tree where each node's left "
            b"subtree contains only values less than the node, and the right subtree "
            b"contains only values greater. Average-case search time is O(log n)."
        )
        temp_resp = client.post("/upload", data={
            "file": (io.BytesIO(handout_text), "bst_handout.txt"),
            "temporary": "true",
        }, content_type="multipart/form-data")
        temp_body = temp_resp.get_json()
        assert temp_resp.status_code == 200
        assert temp_body["temporary"] is True
        extracted_content = temp_body["content"]
        assert "Binary Search Tree" in extracted_content or "binary search tree" in extracted_content.lower()

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM documents WHERE student_id=1")
            assert cur.fetchone()["n"] == 0, "a temporary upload must never be saved as a permanent document"
            cur.close()

        fake = FakeClient(json.dumps([
            {"question": "What is the average-case search time in a BST?",
             "answer": "O(log n).", "explanation": "Stated directly in the handout."}
        ]))
        monkeypatch.setattr(practice_service, "anthropic_client", fake)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)

        resp = client.post("/generate-practice", json={
            "course": "CS 2302", "count": 1, "temp_material": extracted_content,
        })
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert len(body["questions"]) == 1
        assert "O(log n)" in body["questions"][0]["answer"]

        sent = fake.messages.last_call["messages"][0]["content"]
        assert "logarithm" in sent.lower() or "log n" in sent.lower() or "O(log n)" in sent

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as n FROM practice_questions WHERE student_id=1")
            assert cur.fetchone()["n"] == 1
            cur.execute("SELECT COUNT(*) as n FROM documents WHERE student_id=1")
            assert cur.fetchone()["n"] == 0
            cur.close()

    def test_temp_material_combines_with_permanent_material_for_same_course(self, client, monkeypatch):
        import wink.blueprints.chat as chat_bp
        import wink.services.practice as practice_service
        import wink.config as config

        register(client)
        with open(os.path.join(FIXTURES_DIR, "sample_syllabus.docx"), "rb") as f:
            client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")

        fake = FakeClient(REALISTIC_QUESTIONS_JSON)
        monkeypatch.setattr(practice_service, "anthropic_client", fake)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)

        resp = client.post("/generate-practice", json={
            "course": "CIS 3305", "count": 2,
            "temp_material": "Pop quiz handout: extra credit is capped at 5% of the final grade.",
        })
        assert resp.status_code == 200

        sent = fake.messages.last_call["messages"][0]["content"]
        assert "extra credit" in sent.lower(), "the temporary handout content should be included alongside the permanent syllabus"
        assert "Late Work" in sent or "late work" in sent.lower(), "the permanent syllabus content should still be included too"
