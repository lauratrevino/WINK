"""Covers the WINK_ACCESS_CODE registration gate (see config.py) that
restricts real registration to the approved ~25-student Phase 1
population while it's set, and is a no-op when it's unset."""
import re


def _register_payload(email="gatetest@utep.edu", **overrides):
    payload = {
        "first_name": "Test", "last_name": "Student",
        "email": email, "password": "SuperSecret123!",
        "classification": "Freshman", "major": "Business Administration",
        "university": "University of Texas at El Paso", "preferred_language": "",
        "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
        "timezone": "America/Denver",
    }
    payload.update(overrides)
    return payload


def _get_csrf(client):
    r = client.get("/register")
    return r.data.decode(), re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode()).group(1)


class TestAccessCodeGate:
    def test_gate_disabled_by_default_registration_unaffected(self, client, monkeypatch):
        import wink.config as config
        monkeypatch.setattr(config, "WINK_ACCESS_CODE", "")

        html, token = _get_csrf(client)
        assert 'id="access_code"' not in html

        resp = client.post("/register", data={"csrf_token": token, **_register_payload()},
                            headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_wrong_code_rejected(self, client, monkeypatch):
        import wink.config as config
        monkeypatch.setattr(config, "WINK_ACCESS_CODE", "REALCODE1")

        html, token = _get_csrf(client)
        assert 'id="access_code"' in html

        resp = client.post("/register", data={"csrf_token": token, "access_code": "WRONGCODE",
                                                **_register_payload()},
                            headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_missing_code_rejected(self, client, monkeypatch):
        import wink.config as config
        monkeypatch.setattr(config, "WINK_ACCESS_CODE", "REALCODE1")

        _, token = _get_csrf(client)
        resp = client.post("/register", data={"csrf_token": token, **_register_payload()},
                            headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 400

    def test_correct_code_case_and_whitespace_insensitive(self, client, monkeypatch):
        import wink.config as config
        monkeypatch.setattr(config, "WINK_ACCESS_CODE", "REALCODE1")

        _, token = _get_csrf(client)
        resp = client.post("/register", data={"csrf_token": token, "access_code": "  realcode1  ",
                                                **_register_payload()},
                            headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_no_account_created_on_wrong_code(self, client, app, monkeypatch):
        """A rejected access code must not leave a half-created student row
        behind — the check has to happen before any insert, not just
        before the response is sent."""
        import wink.config as config
        from wink.extensions import get_db
        monkeypatch.setattr(config, "WINK_ACCESS_CODE", "REALCODE1")

        _, token = _get_csrf(client)
        email = "shouldnotexist@utep.edu"
        client.post("/register", data={"csrf_token": token, "access_code": "WRONG",
                                        **_register_payload(email=email)},
                    headers={"X-Requested-With": "XMLHttpRequest"})

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", (email,))
            row = cur.fetchone()
            cur.close()
        assert row is None
