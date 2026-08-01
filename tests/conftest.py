"""Pytest fixtures for integration tests against a REAL Postgres database
(not mocked) and a fake Anthropic client (no real API calls / cost)."""
import os
import sys

os.environ.setdefault("DATABASE_URL", "postgres://postgres:testpass@localhost/wink_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-integration-testing")
os.environ.setdefault("ADMIN_EMAIL", "admin@utep.edu")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from wink.extensions import get_db


@pytest.fixture()
def app():
    import app as app_module
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    yield app_module.app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_db(app):
    """Truncate every table before each test so tests don't see each
    other's data, while running against the same real Postgres instance."""
    with app.app_context():
        conn = get_db(); cur = conn.cursor()
        cur.execute("""TRUNCATE students, documents, events, password_resets,
                       deadlines, conversations, rate_limits RESTART IDENTITY CASCADE""")
        conn.commit(); cur.close()
    yield


def mark_email_verified(email):
    """Marks a test student as email-verified directly against Postgres,
    using its own standalone connection rather than Flask's request-scoped
    pool — so it works from a plain helper function, not just from inside
    a request or an app-context block. Most tests register a student to
    test something OTHER than the verification gate itself (uploads, chat,
    practice generation, etc.), so they call this right after registering
    to simulate "already clicked the verification link" — the realistic
    state for what they're actually testing. The gate itself has its own
    dedicated test in test_email_verification_gate.py that deliberately
    does NOT call this."""
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("UPDATE students SET email_verified=TRUE WHERE email=%s", (email,))
    conn.commit()
    cur.close()
    conn.close()
