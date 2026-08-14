import io
import os

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")



def register(client, email="student@utep.edu", password="password123",
             first_name="Ada", last_name="Lovelace", classification="Senior",
             major="Computer Science", university="University of Texas at El Paso"):
    from conftest import mark_email_verified
    resp = client.post("/register", data={
        "email": email, "password": password, "first_name": first_name,
        "last_name": last_name, "classification": classification,
        "major": major, "university": university,
        "terms_agree": "on", "research_agree": "on",
    }, follow_redirects=False)
    mark_email_verified(email)
    return resp


def login(client, email="student@utep.edu", password="password123"):
    return client.post("/login", data={"email": email, "password": password},
                        follow_redirects=False)


class TestRegistrationAndLogin:
    def test_register_creates_real_row_and_logs_in_the_session(self, client, app):
        from wink.extensions import get_db
        resp = register(client)
        assert resp.status_code == 302, resp.get_data(as_text=True)

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM students WHERE email=%s", ("student@utep.edu",))
            row = cur.fetchone(); cur.close()
        assert row is not None, "student row should actually exist in Postgres"
        assert row["first_name"] == "Ada"
        assert row["university"] == "University of Texas at El Paso"
        assert row["password_hash"] != "password123", "password must be hashed, never stored raw"

        dash = client.get("/dashboard")
        assert dash.status_code == 200

    def test_duplicate_registration_is_rejected(self, client):
        register(client)
        resp2 = register(client)
        assert "Account already exists" in resp2.get_data(as_text=True)

    def test_non_edu_email_is_accepted(self, client, app):
        # WINK supports students at any institution, not just UTEP, so
        # registration deliberately accepts any validly-formatted email —
        # there's no .edu-only restriction. This replaces an older test
        # that expected the opposite (a leftover from an earlier,
        # single-university version of the product).
        from wink.extensions import get_db
        resp = register(client, email="student@gmail.com")
        assert resp.status_code == 302, resp.get_data(as_text=True)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", ("student@gmail.com",))
            row = cur.fetchone(); cur.close()
        assert row is not None, "a non-.edu email should be allowed to register"

    def test_login_with_correct_password_succeeds(self, client):
        register(client)
        client.post("/logout")
        resp = login(client)
        assert resp.status_code == 302
        assert client.get("/dashboard").status_code == 200

    def test_login_with_wrong_password_fails(self, client):
        register(client)
        client.post("/logout")
        resp = login(client, password="wrong-password")
        assert "Invalid email or password" in resp.get_data(as_text=True)
        assert client.get("/dashboard").status_code == 302  

    def test_admin_login_redirects_to_analytics(self, client):
        register(client, email="admin@utep.edu")
        client.post("/logout")
        resp = login(client, email="admin@utep.edu")
        assert resp.status_code == 302
        assert "/analytics-page" in resp.headers["Location"]


class TestDocumentUpload:
    def test_upload_real_docx_extracts_real_text(self, client):
        register(client)
        path = os.path.join(FIXTURES_DIR, "sample_syllabus.docx")
        with open(path, "rb") as f:
            resp = client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["success"] is True
        assert body["chars_extracted"] > 500, "should have pulled real text out of the real docx"

        docs_page = client.get("/documents")
        assert docs_page.status_code == 200
        assert b"CIS 3305" in docs_page.data

    def test_upload_real_pdf_extracts_real_text(self, client):
        register(client)
        path = os.path.join(FIXTURES_DIR, "sample_cs_syllabus.pdf")
        with open(path, "rb") as f:
            resp = client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "cs_2302_abet_syllabus.pdf"),
                "course": "CS 2302", "crn": "27062",
            }, content_type="multipart/form-data")
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["chars_extracted"] > 500, "should have pulled real text out of the real pdf"

    def test_reupload_same_course_crn_filename_replaces_not_duplicates(self, client):
        register(client)
        path = os.path.join(FIXTURES_DIR, "sample_syllabus.docx")
        with open(path, "rb") as f:
            content = f.read()
        for _ in range(2):
            resp = client.post("/upload", data={
                "file": (io.BytesIO(content), "Spring2026Syllabus.docx"),
                "course": "CIS 3305", "crn": "12345",
            }, content_type="multipart/form-data")
        assert resp.get_json()["replaced"] is True
        docs = client.get("/documents")
        assert docs.data.count(b"Spring2026Syllabus.docx") <= 4, "one document's filename legitimately appears up to 4x (link text + data attribute, in both the server-rendered and client-rendered rows) — more than that would indicate real duplication"  

    def test_document_cap_enforced(self, client, app):
        register(client)
        from wink import config
        content = b"placeholder text content"
        original_cap = config.MAX_DOCS_PER_STUDENT
        config.MAX_DOCS_PER_STUDENT = 2
        try:
            for i in range(2):
                r = client.post("/upload", data={
                    "file": (io.BytesIO(content), f"doc{i}.txt"),
                    "course": f"COURSE{i}", "crn": str(1000 + i),
                }, content_type="multipart/form-data")
                assert r.get_json()["success"] is True
            r3 = client.post("/upload", data={
                "file": (io.BytesIO(content), "doc_over_cap.txt"),
                "course": "COURSEX", "crn": "9999",
            }, content_type="multipart/form-data")
            assert r3.status_code == 400
            assert "document limit" in r3.get_json()["error"]
        finally:
            config.MAX_DOCS_PER_STUDENT = original_cap

    def test_delete_file_only_deletes_own_document(self, client):
        register(client, email="a@utep.edu")
        client.post("/upload", data={
            "file": (io.BytesIO(b"secret content"), "a_doc.txt"),
            "course": "COURSEA", "crn": "1111",
        }, content_type="multipart/form-data")
        client.get("/documents")
        client.post("/logout")

        register(client, email="b@utep.edu")
        resp = client.post("/delete-file", json={"doc_id": 1})
        assert resp.status_code == 200  

        client.post("/logout")
        login(client, email="a@utep.edu")
        docs_after = client.get("/documents").data
        assert b"a_doc.txt" in docs_after, "student A's document must still exist — B must not be able to delete it"


class TestChatWithRealDBFakeModel:
    def test_chat_saves_real_conversation_row(self, client, monkeypatch):
        register(client)

        class FakeStream:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            @property
            def text_stream(self):
                yield "The "
                yield "answer."
        class FakeMessages:
            def stream(self, **kwargs): return FakeStream()
        class FakeClient:
            messages = FakeMessages()

        import wink.blueprints.chat as chat_bp
        monkeypatch.setattr(chat_bp, "anthropic_client", FakeClient())
        import wink.config as config
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")

        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "What's due this week?"}]})
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == "The answer."

        convs = client.get("/conversations").get_json()["conversations"]
        assert len(convs) == 1
        assert convs[0]["message_count"] == 2  


class TestAdminAnalytics:
    def test_non_admin_cannot_reach_analytics(self, client):
        register(client, email="notadmin@utep.edu")
        assert client.get("/analytics-data").status_code == 403

    def test_admin_sees_real_student_summary(self, client):
        register(client, email="s1@utep.edu")
        client.post("/logout")
        register(client, email="admin@utep.edu")

        data = client.get("/analytics-data").get_json()
        assert data["total_students"] == 2
        emails = {s["email"] for s in data["students"]}
        assert emails == {"s1@utep.edu", "admin@utep.edu"}

    def test_toggle_student_active_persists_to_real_db(self, client, app):
        from wink.extensions import get_db
        register(client, email="s1@utep.edu")
        client.post("/logout")
        register(client, email="admin@utep.edu")

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", ("s1@utep.edu",))
            sid = cur.fetchone()["id"]
            cur.close()

        resp = client.post("/toggle-student-active", json={"student_id": sid})
        assert resp.get_json()["is_active"] is False

        client.post("/logout")
        login_resp = login(client, email="s1@utep.edu")
        assert "suspended" in login_resp.get_data(as_text=True)
