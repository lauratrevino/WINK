

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


class TestPreferredLanguage:
    def test_defaults_to_empty_auto_detect(self, client, app):
        from wink.extensions import get_db
        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT preferred_language FROM students WHERE email=%s", ("student@utep.edu",))
            row = cur.fetchone(); cur.close()
        assert row["preferred_language"] == ""

    def test_update_profile_sets_and_persists_language(self, client, app):
        from wink.extensions import get_db
        register(client)
        resp = client.post("/update-profile", json={
            "first_name": "Ada", "last_name": "Lovelace",
            "classification": "Senior", "major": "Computer Science",
            "university": "University of Texas at El Paso", "preferred_language": "Spanish",
        })
        assert resp.status_code == 200
        assert resp.get_json()["profile"]["preferred_language"] == "Spanish"

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT preferred_language FROM students WHERE email=%s", ("student@utep.edu",))
            row = cur.fetchone(); cur.close()
        assert row["preferred_language"] == "Spanish"

    def test_rejects_unsupported_language(self, client):
        register(client)
        resp = client.post("/update-profile", json={
            "first_name": "Ada", "last_name": "Lovelace",
            "classification": "Senior", "major": "Computer Science",
            "university": "University of Texas at El Paso", "preferred_language": "Klingon",
        })
        assert resp.status_code == 400

    def test_omitting_language_field_leaves_it_untouched(self, client, app):
        from wink.extensions import get_db
        register(client)
        client.post("/update-profile", json={
            "first_name": "Ada", "last_name": "Lovelace",
            "classification": "Senior", "major": "Computer Science",
            "university": "University of Texas at El Paso", "preferred_language": "Spanish",
        })
        client.post("/update-profile", json={
            "first_name": "Ada", "last_name": "Lovelace-Updated",
            "classification": "Senior", "major": "Computer Science",
            "university": "University of Texas at El Paso",
        })
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT last_name, preferred_language FROM students WHERE email=%s", ("student@utep.edu",))
            row = cur.fetchone(); cur.close()
        assert row["last_name"] == "Lovelace-Updated"
        assert row["preferred_language"] == "Spanish", "language set earlier must survive an update that omits the field"
