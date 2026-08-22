import io
import os

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest


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


class TestSpacedRepetitionScheduling:

    def test_correct_answer_roughly_triples_interval(self):
        from wink.services.practice import schedule_next_review
        from datetime import datetime
        from wink import config
        new_interval, next_date = schedule_next_review(1, correct=True)
        assert new_interval == 3
        # schedule_next_review() deliberately anchors to the app's configured
        # local timezone (see its own docstring/config.APP_TIMEZONE), not
        # whatever timezone the test runner's system clock happens to be in
        # — comparing against the bare date.today() here was flaky for
        # several hours a day whenever the two disagreed on the calendar
        # date, exactly the class of bug already fixed elsewhere in this
        # app (extract_deadlines(), the progress-page activity charts).
        local_today = datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()
        assert next_date == local_today + timedelta(days=3)

        new_interval, _ = schedule_next_review(3, correct=True)
        assert new_interval == 9

    def test_incorrect_answer_resets_to_one_day_regardless_of_streak(self):
        from wink.services.practice import schedule_next_review
        from datetime import datetime
        from wink import config
        new_interval, next_date = schedule_next_review(27, correct=False)
        assert new_interval == 1
        local_today = datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()
        assert next_date == local_today + timedelta(days=1)

    def test_interval_is_capped(self):
        from wink.services.practice import schedule_next_review
        new_interval, _ = schedule_next_review(50, correct=True)
        assert new_interval == 60  


class TestSpacedRepetitionRealDB:
    def test_generated_questions_are_stored_and_reviewable(self, client, monkeypatch):
        import wink.blueprints.chat as chat_bp
        import wink.services.practice as practice_service
        import wink.config as config
        import json as json_mod

        register(client)
        with open(os.path.join(FIXTURES_DIR, "sample_syllabus.docx"), "rb") as f:
            temp_resp = client.post("/upload", data={
                "file": (io.BytesIO(f.read()), "Spring2026Syllabus.docx"),
                "temporary": "true",
            }, content_type="multipart/form-data")
        extracted_content = temp_resp.get_json()["content"]

        class FakeTextBlock:
            def __init__(self, text): self.type, self.text = "text", text
        class FakeResponse:
            def __init__(self, text):
                self.content = [FakeTextBlock(text)]
                self.usage = None  # log_token_usage() already handles a falsy usage gracefully
        class FakeMessages:
            def create(self, **kwargs):
                return FakeResponse(json_mod.dumps([
                    {"question": "What is the late work policy?", "answer": "None accepted.", "explanation": "See syllabus."}
                ]))
        class FakeClient:
            messages = FakeMessages()

        monkeypatch.setattr(practice_service, "anthropic_client", FakeClient())
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(chat_bp, "rate_limited", lambda *a, **kw: 0)

        resp = client.post("/generate-practice", json={
            "course": "CIS 3305", "count": 1, "temp_material": extracted_content,
        })
        body = resp.get_json()
        assert resp.status_code == 200, body
        qid = body["questions"][0]["id"]
        assert isinstance(qid, int), "stored questions must come back with a real database id"

        review = client.get("/practice-review", query_string={"course": "CIS 3305"}).get_json()
        assert any(q["id"] == qid for q in review["questions"])

        attempt = client.post("/practice-attempt", json={"question_id": qid, "correct": True})
        assert attempt.status_code == 200
        assert attempt.get_json()["question"]["interval_days"] == 3

        review_after = client.get("/practice-review", query_string={"course": "CIS 3305"}).get_json()
        assert not any(q["id"] == qid for q in review_after["questions"]), \
            "a question just answered correctly should be rescheduled out of today's review"

    def test_cannot_record_attempt_on_another_students_question(self, client, app):
        from wink.extensions import get_db
        register(client, email="a@utep.edu")
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO practice_questions (student_id, course, question, answer)
                           VALUES (%s, 'CIS 3305', 'Q?', 'A.') RETURNING id""", (1,))
            qid = cur.fetchone()["id"]
            conn.commit(); cur.close()

        client.post("/logout")
        register(client, email="b@utep.edu")
        resp = client.post("/practice-attempt", json={"question_id": qid, "correct": True})
        assert resp.status_code == 404, "a student must not be able to update another student's practice question"


class TestDeadlineConflicts:
    def test_detects_a_real_cluster_of_close_deadlines(self, client, app):
        from wink.extensions import get_db
        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            base = date.today() + timedelta(days=10)
            for i, days_offset in enumerate([0, 2, 4]):
                cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date)
                               VALUES (%s, %s, %s, %s)""",
                            (1, f"COURSE{i}", f"Assignment {i}", base + timedelta(days=days_offset)))
            cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date)
                           VALUES (%s, 'COURSEX', 'Final Project', %s)""",
                        (1, base + timedelta(days=60)))
            conn.commit(); cur.close()

        from wink.services.deadlines import detect_deadline_conflicts
        with app.app_context():
            clusters = detect_deadline_conflicts(1, window_days=5, min_items=3)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_no_false_positive_when_deadlines_are_spread_out(self, client, app):
        from wink.extensions import get_db
        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            base = date.today() + timedelta(days=10)
            for i, days_offset in enumerate([0, 20, 40]):
                cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date)
                               VALUES (%s, %s, %s, %s)""",
                            (1, f"COURSE{i}", f"Assignment {i}", base + timedelta(days=days_offset)))
            conn.commit(); cur.close()

        from wink.services.deadlines import detect_deadline_conflicts
        with app.app_context():
            clusters = detect_deadline_conflicts(1, window_days=5, min_items=3)
        assert clusters == []

    def test_endpoint_returns_real_conflicts(self, client, app):
        from wink.extensions import get_db
        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            base = date.today() + timedelta(days=5)
            for i, days_offset in enumerate([0, 1, 2]):
                cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date)
                               VALUES (%s, %s, %s, %s)""",
                            (1, f"COURSE{i}", f"Item {i}", base + timedelta(days=days_offset)))
            conn.commit(); cur.close()
        resp = client.get("/deadline-conflicts")
        body = resp.get_json()
        assert resp.status_code == 200
        assert len(body["conflicts"]) == 1


class TestAnswerFeedback:
    def test_records_and_aggregates_real_feedback(self, client, app):
        from wink.extensions import get_db
        register(client, email="admin@utep.edu")

        for rating in ["up", "up", "down"]:
            resp = client.post("/rate-answer", json={
                "conversation_id": 1, "message_index": 0, "rating": rating,
            })
            assert resp.status_code == 200

        resp_bad = client.post("/rate-answer", json={"conversation_id": 1, "message_index": 0, "rating": "sideways"})
        assert resp_bad.status_code == 400

        with app.app_context():
            from wink.services.analytics import compute_engagement_insights
            conn = get_db(); cur = conn.cursor()
            insights = compute_engagement_insights(cur)
            cur.close()
        assert insights["answer_feedback"]["up"] == 2
        assert insights["answer_feedback"]["down"] == 1
        assert insights["answer_feedback"]["positive_pct"] == pytest.approx(66.7, abs=0.1)


class TestChatAccessibility:
    def test_message_container_has_aria_live_region(self):
        from flask import render_template, g
        import app as app_module
        mock_student = {"id": 1, "email": "test@utep.edu", "first_name": "Test", "last_name": "Student",
                        "classification": "Senior", "major": "Computer Science", "university": "University of Texas at El Paso",
                        "is_active": True, "email_verified": True}
        with app_module.app.test_request_context("/"):
            g.csp_nonce = "test-nonce"
            out = render_template("chat.html", s=mock_student, admin_email="lhall@utep.edu", active="chat")
        assert 'id="messages" role="log" aria-live="polite"' in out


class TestSourceCitationInstruction:
    def test_chat_system_prompt_instructs_specific_citation(self):
        import inspect
        import wink.services.system_prompt as system_prompt
        source = inspect.getsource(system_prompt.build_chat_instructions)
        assert "CITE YOUR SOURCE SPECIFICALLY" in source
        assert "name the actual file" in source


class TestCommonQuestions:
    def test_groups_identical_questions_from_multiple_students(self, client, app):
        from wink.extensions import get_db
        from wink.services.analytics import log_event, compute_engagement_insights

        for i, email in enumerate(["a@utep.edu", "b@utep.edu", "c@utep.edu"]):
            client.post("/register", data={
                "email": email, "password": "password123",
                "first_name": "S", "last_name": str(i),
                "classification": "Senior", "major": "Computer Science", "university": "University of Texas at El Paso",
                "terms_agree": "on", "research_agree": "on",
            })
        with app.app_context():
            log_event(1, "question_asked", {"q": "What is the late work policy?"})
            log_event(2, "question_asked", {"q": "What is the late work policy?"})
            log_event(3, "question_asked", {"q": "When is office hours?"})

            conn = get_db(); cur = conn.cursor()
            insights = compute_engagement_insights(cur)
            cur.close()

        common = insights["common_questions"]
        assert len(common) == 1, "only the question asked by 2+ distinct students should surface"
        assert common[0]["question"] == "What is the late work policy?"
        assert common[0]["n_students"] == 2
        assert common[0]["n"] == 2

    def test_endpoint_surfaces_common_questions(self, client):
        resp = client.get("/analytics-data")
        assert resp.status_code in (302, 401, 403)


class TestDiagramInstruction:
    def test_chat_system_prompt_instructs_mermaid_diagrams(self):
        import inspect
        import wink.services.system_prompt as system_prompt
        source = inspect.getsource(system_prompt.build_chat_instructions)
        assert "mermaid" in source.lower()
        assert "flowchart" in source.lower()


class TestStudyPlan:
    def test_groups_deadlines_into_the_right_week(self, client, app):
        from wink.extensions import get_db
        from datetime import date, timedelta
        register(client)
        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date)
                           VALUES (1, 'CS 2302', 'Quiz 1', %s)""", (date.today() + timedelta(days=2),))
            cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date)
                           VALUES (1, 'CS 2302', 'Final Project', %s)""", (date.today() + timedelta(days=22),))
            conn.commit(); cur.close()

        resp = client.get("/study-plan", query_string={"weeks": 4})
        body = resp.get_json()
        assert resp.status_code == 200
        weeks = body["weeks"]
        assert len(weeks) == 4
        assert any(d["title"] == "Quiz 1" for d in weeks[0]["deadlines"])
        assert not any(d["title"] == "Final Project" for d in weeks[0]["deadlines"])
        assert any(d["title"] == "Final Project" for d in weeks[3]["deadlines"])


class TestWrapped:
    def test_real_stats_computed_from_real_activity(self, client, app):
        from wink.extensions import get_db
        from wink.services.analytics import log_event, get_wrapped_stats
        import io

        register(client)
        client.post("/upload", data={
            "file": (io.BytesIO(b"syllabus content"), "syllabus.txt"),
            "course": "CS 2302", "crn": "111",
        }, content_type="multipart/form-data")

        with app.app_context():
            log_event(1, "question_asked", {"q": "Q1"})
            log_event(1, "question_asked", {"q": "Q2"})
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO practice_questions (student_id, course, question, answer, correct_streak)
                           VALUES (1, 'CS 2302', 'Q?', 'A.', 1)""")
            conn.commit(); cur.close()

            stats = get_wrapped_stats(1)

        assert stats["total_questions"] == 2
        assert stats["questions_mastered"] == 1
        assert any(c["course"] == "CS 2302" for c in stats["courses"])

    def test_wrapped_data_endpoint_real_response(self, client):
        register(client)
        resp = client.get("/wrapped-data")
        assert resp.status_code == 200

    def test_wrapped_page_renders(self, client):
        register(client)
        resp = client.get("/wrapped-page")
        assert resp.status_code == 200
        assert b"Wrapped" in resp.data


class TestReminderDeliveryTracking:
    def test_failed_email_does_not_mark_that_students_deadlines_reminded(self, client, app, monkeypatch):
        from wink.extensions import get_db
        from datetime import date, timedelta
        import wink.blueprints.calendar as calendar_bp

        for email in ["ok@utep.edu", "fails@utep.edu"]:
            register(client, email=email)

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date, reminded)
                           VALUES (1, 'CS 2302', 'Quiz', %s, FALSE) RETURNING id""",
                        (date.today() + timedelta(days=1),))
            ok_id = cur.fetchone()["id"]
            cur.execute("""INSERT INTO deadlines (student_id, course, title, due_date, reminded)
                           VALUES (2, 'CS 2302', 'Quiz', %s, FALSE) RETURNING id""",
                        (date.today() + timedelta(days=1),))
            fail_id = cur.fetchone()["id"]
            conn.commit(); cur.close()

        def fake_send_email(to_email, subject, body):
            return to_email == "ok@utep.edu"  

        monkeypatch.setattr(calendar_bp, "send_email", fake_send_email)
        monkeypatch.setattr(calendar_bp.config, "CRON_SECRET", "test-secret")

        resp = client.post("/send-deadline-reminders", headers={"X-WINK-Cron-Secret": "test-secret"})
        assert resp.status_code == 200

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT reminded FROM deadlines WHERE id=%s", (ok_id,))
            ok_reminded = cur.fetchone()["reminded"]
            cur.execute("SELECT reminded FROM deadlines WHERE id=%s", (fail_id,))
            fail_reminded = cur.fetchone()["reminded"]
            cur.close()

        assert ok_reminded is True, "the student whose email succeeded should be marked reminded"
        assert fail_reminded is False, "the student whose email FAILED must stay reminded=False so they're retried"
