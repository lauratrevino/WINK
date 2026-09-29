"""MAX_REGISTRATIONS Beta cap: once full, sign-up shows a waitlist instead,
new registrations are refused, and waitlist entries are saved."""
import os
import re

import psycopg2

from test_access_code_gate import _register_payload


def _csrf(client, path="/register"):
    html = client.get(path).data.decode()
    return html, re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _waitlist_emails():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("SELECT email FROM waitlist")
    rows = [r[0] for r in cur.fetchall()]
    cur.execute("DELETE FROM waitlist")
    conn.commit(); conn.close()
    return rows


class TestRegistrationCap:
    def test_under_cap_registration_open(self, client, monkeypatch):
        import wink.config as config
        monkeypatch.setattr(config, "MAX_REGISTRATIONS", 5)
        html, token = _csrf(client)
        assert 'id="register-form"' in html and "waitlist-form" not in html
        resp = client.post("/register", data={"csrf_token": token, **_register_payload("capa@utep.edu")},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.get_json()["success"] is True

    def test_full_beta_shows_waitlist_and_refuses_registration(self, client, monkeypatch):
        import wink.config as config
        monkeypatch.setattr(config, "MAX_REGISTRATIONS", 1)
        _, token = _csrf(client)
        client.post("/register", data={"csrf_token": token, **_register_payload("first@utep.edu")},
                    headers={"X-Requested-With": "XMLHttpRequest"})
        client.post("/logout")
        html, token = _csrf(client)
        assert "waitlist-form" in html and 'id="register-form"' not in html
        resp = client.post("/register", data={"csrf_token": token, **_register_payload("second@utep.edu")},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 400 and "full" in resp.get_json()["error"]

        resp = client.post("/waitlist", data={"csrf_token": token, "email": "Later@School.edu",
                                              "first_name": "Ana", "university": "Somewhere"})
        assert "on the waitlist" in resp.data.decode()
        assert _waitlist_emails() == ["later@school.edu"]

    def test_zero_means_no_cap(self, client, monkeypatch):
        import wink.config as config
        monkeypatch.setattr(config, "MAX_REGISTRATIONS", 0)
        html, _ = _csrf(client)
        assert 'id="register-form"' in html
