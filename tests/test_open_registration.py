"""Registration is open: no access code field is shown and students can
create an account with just the normal sign-up fields."""
import re


def _register_payload(email="opentest@utep.edu", **overrides):
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


class TestOpenRegistration:
    def test_no_access_code_field(self, client):
        html, _ = _get_csrf(client)
        assert 'id="access_code"' not in html
        assert "Access Code" not in html

    def test_register_without_code(self, client):
        _, token = _get_csrf(client)
        resp = client.post("/register", data={"csrf_token": token, **_register_payload()},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_stray_access_code_is_ignored(self, client):
        _, token = _get_csrf(client)
        resp = client.post("/register", data={"csrf_token": token, "access_code": "ANYTHING",
                                               **_register_payload("stray@utep.edu")},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
