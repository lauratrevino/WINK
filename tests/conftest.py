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
    with app.app_context():
        conn = get_db(); cur = conn.cursor()
        cur.execute("""TRUNCATE students, documents, events, password_resets,
                       deadlines, conversations, rate_limits RESTART IDENTITY CASCADE""")
        conn.commit(); cur.close()
    yield


def mark_email_verified(email):
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("UPDATE students SET email_verified=TRUE WHERE email=%s", (email,))
    conn.commit()
    cur.close()
    conn.close()
