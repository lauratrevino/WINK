"""Regression tests for the release-verdict fixes:
- deterministic weekday/relative-date labeling (the confirmed live-pilot
  date-interpretation defect)
- negative `days=` on /deadlines
- grading-weight validation (NaN, duplicate/empty category, count cap,
  tolerance, empty-list clearing)
- centralized cron auth/run-logging (services/cron.py)
- the "Other" university free-text field
"""
import math
from datetime import date, datetime, timedelta

import pytest


def register(client, email="student@utep.edu", password="password123",
             first_name="Ada", last_name="Lovelace", classification="Senior",
             major="Computer Science", university="University of Texas at El Paso",
             other_university_name=None):
    from conftest import mark_email_verified
    form = {
        "email": email, "password": password, "first_name": first_name,
        "last_name": last_name, "classification": classification,
        "major": major, "university": university,
        "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
    }
    if other_university_name is not None:
        form["other_university_name"] = other_university_name
    resp = client.post("/register", data=form, follow_redirects=False)
    mark_email_verified(email)
    return resp


def login(client, email="student@utep.edu", password="password123"):
    return client.post("/login", data={"email": email, "password": password},
                        follow_redirects=False)


class TestRelativeDayLabel:
    """Pure-function coverage for the deterministic date-diff helper that
    replaced asking the AI model to compute weekday/relative-date math
    itself — the live pilot showed the model mislabeling a correct,
    database-sourced date (a Monday called "Sunday"; a date two days out
    called "tomorrow")."""

    def test_today(self):
        from wink.timeutil import relative_day_label
        d = date(2026, 9, 5)
        assert relative_day_label(d, d) == "today"

    def test_tomorrow(self):
        from wink.timeutil import relative_day_label
        today = date(2026, 9, 5)
        assert relative_day_label(today + timedelta(days=1), today) == "tomorrow"

    def test_yesterday(self):
        from wink.timeutil import relative_day_label
        today = date(2026, 9, 5)
        assert relative_day_label(today - timedelta(days=1), today) == "yesterday"

    def test_several_days_out(self):
        from wink.timeutil import relative_day_label
        today = date(2026, 9, 5)
        assert relative_day_label(today + timedelta(days=2), today) == "in 2 days"

    def test_several_days_ago(self):
        from wink.timeutil import relative_day_label
        today = date(2026, 9, 5)
        assert relative_day_label(today - timedelta(days=3), today) == "3 days ago"

    def test_month_boundary(self):
        from wink.timeutil import relative_day_label
        # Aug 31, 2026 -> Sep 1, 2026 must still read as "tomorrow", not
        # get lost in a naive string/day-of-month comparison.
        today = date(2026, 8, 31)
        assert relative_day_label(date(2026, 9, 1), today) == "tomorrow"

    def test_year_boundary(self):
        from wink.timeutil import relative_day_label
        today = date(2026, 12, 31)
        assert relative_day_label(date(2027, 1, 1), today) == "tomorrow"


class TestDateReferenceBlock:
    def test_sept_7_2026_is_labeled_monday(self):
        # The exact case from the confirmed live-pilot defect: WINK called
        # Sept 7, 2026 "Sunday" and, separately, called it "tomorrow" when
        # the server date was Sept 5. Sept 7, 2026 is actually a Monday,
        # and relative to Sept 5 it's two days out, not one.
        from wink.timeutil import build_date_reference_block
        from zoneinfo import ZoneInfo
        now = datetime(2026, 9, 5, 9, 0, tzinfo=ZoneInfo("America/Denver"))
        block = build_date_reference_block(now)
        assert "2026-09-07 = Monday (in 2 days)" in block
        assert "2026-09-05 = Saturday (today)" in block
        assert "2026-09-06 = Sunday (tomorrow)" in block

    def test_covers_fourteen_days(self):
        from wink.timeutil import build_date_reference_block
        from zoneinfo import ZoneInfo
        now = datetime(2026, 1, 1, tzinfo=ZoneInfo("America/Denver"))
        block = build_date_reference_block(now)
        # 1 header line + 14 date lines
        assert len(block.splitlines()) == 15


class TestDeadlinesContextDateLabels:
    def test_deadline_entry_includes_weekday_and_relative_label(self, client, app):
        from wink.extensions import get_db
        from wink.services.deadlines import build_deadlines_context
        from zoneinfo import ZoneInfo

        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", ("student@utep.edu",))
            sid = cur.fetchone()["id"]
            cur.execute("""INSERT INTO documents(student_id, filename, orig_name, course, content)
                           VALUES (%s, 'syllabus.pdf', 'syllabus.pdf', 'ENGL 1301', 'x') RETURNING id""", (sid,))
            doc_id = cur.fetchone()["id"]
            cur.execute("""INSERT INTO deadlines(student_id, document_id, course, title, due_date, status)
                           VALUES (%s, %s, 'ENGL 1301', 'Essay 1', '2026-09-07', 'confirmed')""",
                        (sid, doc_id))
            conn.commit()

            now = datetime(2026, 9, 5, 9, 0, tzinfo=ZoneInfo("America/Denver"))
            ctx = build_deadlines_context(sid, now=now)
            cur.close()
        assert "Monday, 2026-09-07 (in 2 days)" in ctx

    def test_no_now_falls_back_to_raw_date_without_crashing(self, client, app):
        # Callers that don't have a student-local "now" handy (none exist
        # today, but the parameter is optional) must not crash.
        from wink.extensions import get_db
        from wink.services.deadlines import build_deadlines_context

        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", ("student@utep.edu",))
            sid = cur.fetchone()["id"]
            cur.execute("""INSERT INTO documents(student_id, filename, orig_name, course, content)
                           VALUES (%s, 'syllabus.pdf', 'syllabus.pdf', 'ENGL 1301', 'x') RETURNING id""", (sid,))
            doc_id = cur.fetchone()["id"]
            cur.execute("""INSERT INTO deadlines(student_id, document_id, course, title, due_date, status)
                           VALUES (%s, %s, 'ENGL 1301', 'Essay 1', '2026-09-07', 'confirmed')""",
                        (sid, doc_id))
            conn.commit()
            ctx = build_deadlines_context(sid, now=None)
            cur.close()
        assert "2026-09-07" in ctx


class TestNegativeDeadlineWindow:
    def test_negative_days_rejected(self, client):
        register(client)
        login(client)
        resp = client.get("/deadlines?days=-100")
        assert resp.status_code == 400

    def test_non_integer_days_rejected(self, client):
        register(client)
        login(client)
        resp = client.get("/deadlines?days=abc")
        assert resp.status_code == 400

    def test_zero_days_allowed(self, client):
        register(client)
        login(client)
        resp = client.get("/deadlines?days=0")
        assert resp.status_code == 200

    def test_large_days_clamped_not_rejected(self, client):
        register(client)
        login(client)
        resp = client.get("/deadlines?days=99999")
        assert resp.status_code == 200


class TestGradingWeightValidation:
    def test_nan_weight_rejected(self, client):
        register(client)
        login(client)
        resp = client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": float("nan")}],
        })
        assert resp.status_code == 400

    def test_infinity_weight_rejected(self, client):
        register(client)
        login(client)
        resp = client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": float("inf")}],
        })
        assert resp.status_code == 400

    def test_empty_category_name_rejected(self, client):
        register(client)
        login(client)
        resp = client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "  ", "weight": 50}],
        })
        assert resp.status_code == 400

    def test_duplicate_category_rejected(self, client):
        register(client)
        login(client)
        resp = client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": 40}, {"category": "exams", "weight": 20}],
        })
        assert resp.status_code == 400

    def test_too_many_categories_rejected(self, client):
        register(client)
        login(client)
        weights = [{"category": f"Cat{i}", "weight": 1} for i in range(25)]
        resp = client.post("/save-grading-weights", json={"course": "ENGL 1301", "weights": weights})
        assert resp.status_code == 400

    def test_total_just_over_100_rejected(self, client):
        register(client)
        login(client)
        resp = client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": 60}, {"category": "Homework", "weight": 40.5}],
        })
        assert resp.status_code == 400

    def test_valid_weights_saved(self, client):
        register(client)
        login(client)
        resp = client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": 60}, {"category": "Homework", "weight": 40}],
        })
        assert resp.status_code == 200
        got = client.get("/grading-weights?course=ENGL 1301").get_json()
        assert {w["category"] for w in got["weights"]} == {"Exams", "Homework"}

    def test_empty_list_clears_weights(self, client):
        register(client)
        login(client)
        client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": 60}],
        })
        resp = client.post("/save-grading-weights", json={"course": "ENGL 1301", "weights": []})
        assert resp.status_code == 200
        assert resp.get_json().get("cleared") is True
        got = client.get("/grading-weights?course=ENGL 1301").get_json()
        assert got["weights"] == []

    def test_nan_never_reaches_storage_as_a_silent_wipe(self, client):
        """The exact failure mode described in the release verdict: a NaN
        weight should be rejected outright (400), never accepted (200)
        while quietly deleting the student's existing valid weights and
        inserting nothing in their place."""
        register(client)
        login(client)
        client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": 60}, {"category": "Homework", "weight": 40}],
        })
        bad = client.post("/save-grading-weights", json={
            "course": "ENGL 1301",
            "weights": [{"category": "Exams", "weight": float("nan")}],
        })
        assert bad.status_code == 400
        still_there = client.get("/grading-weights?course=ENGL 1301").get_json()
        assert {w["category"] for w in still_there["weights"]} == {"Exams", "Homework"}


class TestCronCentralization:
    def test_wrong_secret_rejected_on_all_four_jobs(self, client, monkeypatch):
        from wink import config
        monkeypatch.setattr(config, "CRON_SECRET", "the-real-secret")
        for path in ("/send-deadline-reminders", "/send-weekly-digest",
                     "/purge-deleted-conversations", "/purge-expired-demos"):
            resp = client.post(path, headers={"X-WINK-Cron-Secret": "wrong"})
            assert resp.status_code == 403, path

    def test_correct_secret_accepted_and_logged(self, client, monkeypatch, app):
        from wink import config
        from wink.extensions import get_db
        monkeypatch.setattr(config, "CRON_SECRET", "the-real-secret")
        resp = client.post("/purge-deleted-conversations",
                            headers={"X-WINK-Cron-Secret": "the-real-secret"})
        assert resp.status_code == 200
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("""SELECT * FROM cron_runs WHERE job_name='purge_deleted_conversations'
                           ORDER BY started_at DESC LIMIT 1""")
            row = cur.fetchone(); cur.close()
        assert row is not None
        assert row["completed_at"] is not None

    def test_bearer_token_form_also_accepted(self, client, monkeypatch):
        from wink import config
        monkeypatch.setattr(config, "CRON_SECRET", "the-real-secret")
        resp = client.post("/purge-expired-demos",
                            headers={"Authorization": "Bearer the-real-secret"})
        assert resp.status_code == 200

    def test_weekly_digest_skips_without_creating_a_run_row_twice_in_a_week(self, client, monkeypatch, app):
        from wink import config
        from wink.extensions import get_db
        monkeypatch.setattr(config, "CRON_SECRET", "the-real-secret")
        first = client.post("/send-weekly-digest", headers={"X-WINK-Cron-Secret": "the-real-secret"})
        assert first.status_code == 200
        second = client.post("/send-weekly-digest", headers={"X-WINK-Cron-Secret": "the-real-secret"})
        assert second.status_code == 200
        assert second.get_json().get("skipped") is True


class TestOtherUniversity:
    def test_registration_requires_name_when_other_selected(self, client):
        resp = register(client, university="Other", other_university_name=None)
        assert resp.status_code == 200  # re-renders the form with an error
        assert b"what school or organization" in resp.data.lower()

    def test_registration_stores_other_university_name(self, client, app):
        from wink.extensions import get_db
        register(client, university="Other", other_university_name="Coronado High School")
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT university, university_other_name FROM students WHERE email=%s",
                        ("student@utep.edu",))
            row = cur.fetchone(); cur.close()
        assert row["university"] == "Other"
        assert row["university_other_name"] == "Coronado High School"

    def test_other_name_ignored_when_real_university_selected(self, client, app):
        from wink.extensions import get_db
        register(client, university="University of Texas at El Paso",
                  other_university_name="should be ignored")
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT university_other_name FROM students WHERE email=%s",
                        ("student@utep.edu",))
            row = cur.fetchone(); cur.close()
        assert row["university_other_name"] is None


class TestRateLimitKeyedByIpAndEmail:
    """A pure per-IP rate-limit key meant one attacker spraying attempts
    (any emails) from a shared IP — dorm wifi, a campus lab, a NAT — could
    exhaust the whole IP's budget and lock out a different real student
    behind the same IP, even one logging in correctly with their own
    account. Keying by IP+email instead gives each account its own bucket."""

    def test_failed_logins_for_one_email_dont_block_another_email_same_ip(self, client):
        register(client, email="victim@utep.edu")
        # Exhaust the login rate limit against a DIFFERENT email, from the
        # same test client (same source IP as far as the server can tell).
        for _ in range(10):
            client.post("/login", data={"email": "someone-else@utep.edu", "password": "wrong"})
        # The real account, same IP, must still be able to log in —
        # a pure per-IP key would have blocked this with a 429-equivalent
        # "Too many attempts" error page instead.
        resp = client.post("/login", data={"email": "victim@utep.edu", "password": "password123"})
        assert b"Too many attempts" not in resp.data

    def test_repeated_failed_logins_for_the_same_email_still_rate_limited(self, client):
        register(client, email="target@utep.edu")
        responses = [client.post("/login", data={"email": "target@utep.edu", "password": "wrong"})
                     for _ in range(11)]
        assert any(b"Too many attempts" in r.data for r in responses)

    def test_registration_spam_for_different_emails_same_ip_not_cross_blocked(self, client):
        for i in range(8):
            client.post("/register", data={
                "email": f"spam{i}@utep.edu", "password": "password123",
                "first_name": "A", "last_name": "B", "classification": "Senior",
                "major": "Computer Science", "university": "University of Texas at El Paso",
                "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
            })
        resp = client.post("/register", data={
            "email": "notspam@utep.edu", "password": "password123",
            "first_name": "A", "last_name": "B", "classification": "Senior",
            "major": "Computer Science", "university": "University of Texas at El Paso",
            "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
        })
        assert b"Too many attempts" not in resp.data

    def test_registration_flood_from_one_ip_still_capped_even_across_many_emails(self, client):
        # The per-(IP, email) key alone would let this through forever —
        # a fresh email each time is a fresh bucket. The coarser per-IP
        # ceiling is what actually stops it.
        responses = [client.post("/register", data={
            "email": f"flood{i}@utep.edu", "password": "password123",
            "first_name": "A", "last_name": "B", "classification": "Senior",
            "major": "Computer Science", "university": "University of Texas at El Paso",
            "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
        }) for i in range(51)]
        assert any(b"Too many attempts from this network" in r.data for r in responses)

