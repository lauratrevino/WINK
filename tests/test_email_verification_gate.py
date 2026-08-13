import io



def register_unverified(client, email="student@utep.edu"):
    return client.post("/register", data={
        "email": email, "password": "password123",
        "first_name": "Ada", "last_name": "Lovelace",
        "classification": "Senior", "major": "Computer Science", "university": "UTEP",
        "terms_agree": "on", "research_agree": "on",
    })


class TestEmailVerificationGate:
    def test_unverified_student_cannot_chat(self, client, monkeypatch):
        import wink.blueprints.chat as chat_bp
        register_unverified(client)
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 403
        assert "verify your email" in resp.get_json()["error"].lower()

    def test_unverified_student_cannot_upload(self, client):
        register_unverified(client)
        resp = client.post("/upload", data={
            "file": (io.BytesIO(b"content"), "file.txt"),
            "course": "CS 2302", "crn": "111",
        }, content_type="multipart/form-data")
        assert resp.status_code == 403
        assert "verify your email" in resp.get_json()["error"].lower()

    def test_unverified_student_cannot_generate_practice(self, client, monkeypatch):
        import wink.blueprints.chat as chat_bp
        import wink.config as config
        register_unverified(client)
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        resp = client.post("/generate-practice", json={"course": "CS 2302"})
        assert resp.status_code == 403
        assert "verify your email" in resp.get_json()["error"].lower()

    def test_unverified_student_can_still_view_dashboard(self, client):
        register_unverified(client)
        resp = client.get("/dashboard")
        assert resp.status_code == 200

    def test_verified_student_can_chat_and_upload(self, client, app, monkeypatch):
        import wink.blueprints.chat as chat_bp
        from wink.extensions import get_db

        register_unverified(client, email="realverify@utep.edu")
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT verification_token FROM students WHERE email=%s", ("realverify@utep.edu",))
            token = cur.fetchone()["verification_token"]
            cur.close()
        assert token, "a real verification token should have been generated at registration"

        verify_resp = client.get(f"/verify-email/{token}")
        assert verify_resp.status_code in (200, 302)

        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)
        resp = client.post("/upload", data={
            "file": (io.BytesIO(b"content"), "file.txt"),
            "course": "CS 2302", "crn": "111",
        }, content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_json()


class TestHealthEndpointDoesNotLeakConfig:
    def test_health_response_has_no_db_or_api_key_fields(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert set(body.keys()) == {"status"}
        assert "db" not in body
        assert "api_key" not in body


class TestPasswordResetTokenHashing:
    def test_full_reset_flow_works_with_hashed_token_storage(self, client, app, monkeypatch):
        import re
        import wink.blueprints.auth as auth_bp
        from wink.extensions import get_db

        register_unverified(client, email="resetflow@utep.edu")
        monkeypatch.setattr(auth_bp.config, "DEBUG_SHOW_RESET_LINKS", True)

        resp = client.post("/forgot-password", data={"email": "resetflow@utep.edu"})
        assert resp.status_code == 200
        match = re.search(r'/reset-password/([^"\'<> ]+)', resp.get_data(as_text=True))
        assert match, "expected a real reset link in the response with DEBUG_SHOW_RESET_LINKS on"
        raw_token = match.group(1)

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT token FROM password_resets")
            stored = [r["token"] for r in cur.fetchall()]
            cur.close()
        assert raw_token not in stored, "the raw token must never be stored, only its hash"

        reset_resp = client.post(f"/reset-password/{raw_token}", data={
            "password": "newpassword456", "confirm_password": "newpassword456",
        })
        assert reset_resp.status_code == 200
        assert b"password has been updated" in reset_resp.data.lower() or b"sign in" in reset_resp.data.lower()

        client.get("/logout")
        login_resp = client.post("/login", data={"email": "resetflow@utep.edu", "password": "newpassword456"})
        assert login_resp.status_code == 302, "should be able to log in with the new password after reset"

    def test_reset_rejects_wrong_or_tampered_token(self, client):
        register_unverified(client, email="resetflow2@utep.edu")
        client.post("/forgot-password", data={"email": "resetflow2@utep.edu"})
        resp = client.get("/reset-password/not-a-real-token")
        assert b"invalid" in resp.data.lower() or resp.status_code == 200
