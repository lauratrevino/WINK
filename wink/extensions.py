"""
Shared, long-lived resources: the Postgres connection pool, the Anthropic
API client, and CSRF protection. Each of these is expensive or stateful
enough that it should be built once per process and reused, never created
per-request or per-module-import in more than one place.
"""
import traceback

import httpx
import anthropic as ac
import psycopg2
from psycopg2 import pool as _pg_pool
from psycopg2.extras import RealDictCursor
from flask import g

from . import config

# ── CSRF ──────────────────────────────────────────────────────
# CSRFProtect supports the Flask app-factory pattern natively: build it here
# with no app, attach it to the real app later in create_app() via
# csrf.init_app(app). Blueprints can safely `from .extensions import csrf`
# and use `@csrf.exempt` at import time (e.g. for the external-scheduler
# /send-deadline-reminders endpoint) regardless of init order.
try:
    from flask_wtf import CSRFProtect
    csrf = CSRFProtect()
except ImportError:
    class _NoOpCSRF:
        """Fallback so the app still boots (with CSRF protection OFF) if
        flask-wtf isn't actually installed in the running environment. This
        is a fail-safe, not a fix — if this branch is active, the deployed
        image was built from a stale layer that never re-ran
        `pip install -r requirements.txt` after flask-wtf was added there.
        Trigger a clean rebuild (not just a restart), then redeploy — don't
        leave CSRF disabled."""
        def init_app(self, app):
            print(
                "WARNING: flask-wtf is not installed in this environment, "
                "even though it's listed in requirements.txt. CSRF "
                "PROTECTION IS DISABLED. Trigger a clean rebuild of the "
                "deploy image (not just a restart) so pip actually "
                "reinstalls from the current requirements.txt, then "
                "redeploy."
            )
            # Every template calls {{ csrf_token() }} — without this
            # fallback, rendering ANY page would throw a Jinja
            # UndefinedError. The emitted token is inert here; it isn't
            # validated by anything until flask-wtf is actually installed.
            app.jinja_env.globals.setdefault("csrf_token", lambda: "")

        def exempt(self, f):
            return f

    csrf = _NoOpCSRF()

# ── Anthropic client ──────────────────────────────────────────
# Build the client once, at import time, and reuse it for every request.
# Creating a fresh httpx.Client per request means a brand-new TCP + TLS
# handshake to Anthropic's servers on every single question. Reusing one
# client with a connection pool lets requests reuse an already-open,
# already-authenticated connection — this alone typically saves 100-300ms
# of pure connection setup time before the model even starts thinking.
_http_client = httpx.Client(
    timeout=httpx.Timeout(110.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
    http2=True,
)
anthropic_client = (
    ac.Anthropic(api_key=config.ANTHROPIC_API_KEY, http_client=_http_client)
    if config.ANTHROPIC_API_KEY else None
)

# ── Voyage AI embeddings client (optional) ────────────────────
# Same graceful-absence pattern as the OCR import in services/documents.py:
# if the package isn't installed, or VOYAGE_API_KEY isn't set, this stays
# None and services/retrieval.py's rank_chunks() falls back to TF-IDF —
# never a hard failure either way.
voyage_client = None
if config.VOYAGE_API_KEY:
    try:
        import voyageai
        voyage_client = voyageai.Client(api_key=config.VOYAGE_API_KEY)
    except ImportError:
        print("VOYAGE_API_KEY is set but the voyageai package isn't installed — "
              "falling back to TF-IDF retrieval. Add voyageai to requirements.txt "
              "and redeploy to enable neural embeddings.")

# ── DB pool ───────────────────────────────────────────────────
# Pooling connections instead of opening a brand-new TCP + auth handshake to
# Postgres on every single query matters a lot under concurrent load — a
# typical request makes several DB round trips (auth check, doc lookup,
# event log, ...). Pool size is configurable via DB_POOL_MIN/DB_POOL_MAX
# (see config.py) so it can be tuned to worker/thread count and expected
# concurrent students without a code change — important once this is running
# for hundreds of students across multiple schools/instances.
_db_pool = None
if config.DB_URL:
    try:
        _db_pool = _pg_pool.ThreadedConnectionPool(
            config.DB_POOL_MIN, config.DB_POOL_MAX, config.DB_URL,
            cursor_factory=RealDictCursor
        )
    except Exception as e:
        print(f"DB pool init failed, falling back to per-request connections: {e}")
        _db_pool = None


def get_db():
    """Returns one connection per request, cached on Flask's `g`. The first
    call in a request checks a connection out of the pool; every subsequent
    call in that same request reuses it instead of checking out another.
    Release back to the pool is guaranteed exactly once per request by the
    teardown handler registered in init_app() below, even after an
    exception — callers never release the connection themselves, only the
    cursor (cur.close()).

    This holds even for streaming responses (see /chat, which uses
    stream_with_context): Flask tears down the request/app context — and
    fires this module's teardown handler, releasing the connection — as
    soon as the view function returns the Response object, before the
    streaming generator body actually runs. stream_with_context re-enters
    that (already torn down) context only so request/g/session reads still
    resolve correctly while the generator streams; it does not keep the
    original connection checked out for the streaming duration. (Verified
    directly against a real Postgres pool: a slow fake model response
    sleeping inside the generator does not delay when the connection is
    released — teardown fires, and the connection goes back to the pool,
    before that sleep even starts.)"""
    if getattr(g, "_db_conn", None) is not None:
        return g._db_conn
    if _db_pool:
        conn = _db_pool.getconn()
    else:
        conn = psycopg2.connect(config.DB_URL, cursor_factory=RealDictCursor)
    g._db_conn = conn
    return conn


def init_app(app):
    """Wires the connection-pool teardown handler into the given Flask app
    and initializes CSRF protection. Called once from create_app()."""
    csrf.init_app(app)

    @app.teardown_appcontext
    def _release_request_db_connection(exception=None):
        conn = g.pop("_db_conn", None)
        if conn is None:
            return
        if _db_pool:
            try:
                _db_pool.putconn(conn)
            except Exception:
                pass
        else:
            try:
                conn.close()
            except Exception:
                pass


def init_db():
    """Creates every table/index this app needs if it doesn't already
    exist. Safe to call on every process start — CREATE TABLE/INDEX IF NOT
    EXISTS and ADD COLUMN IF NOT EXISTS make this idempotent."""
    if not config.DB_URL:
        print("WARNING: No DATABASE_URL set.")
        return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS students (
            id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, first_name TEXT NOT NULL,
            last_name TEXT NOT NULL, classification TEXT NOT NULL,
            major TEXT NOT NULL, university TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS university TEXT DEFAULT ''")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS verification_token TEXT")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS preferred_language TEXT DEFAULT ''")
        cur.execute("""CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, orig_name TEXT NOT NULL,
            course TEXT NOT NULL, size_bytes INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            uploaded_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT DEFAULT ''")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS crn TEXT DEFAULT ''")
        # Only meaningful on global/admin-uploaded rows (student_id IS NULL) —
        # scopes each general-reference document to one school's knowledge
        # base, since WINK serves more than just UTEP.
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS university TEXT DEFAULT ''")
        # 'material' (default) — course content (syllabus, notes, slides).
        # 'assessment' — a past exam/quiz/study guide the student uploads
        # specifically as a style example for generate_practice_questions()
        # (see services/practice.py) — never used as the factual content
        # source for practice questions, only as a format/style reference.
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type TEXT DEFAULT 'material'")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_global_university ON documents(university) WHERE student_id IS NULL")
        # Use TEXT for payload — simple and reliable across all Postgres versions
        cur.execute("""CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY, student_id INTEGER,
            event_type TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS password_resets (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW())""")
        # NOTE: despite the column name, this stores a SHA-256 hash of the
        # reset token, not the raw token — see auth.py's forgot_password()/
        # reset_password(). The raw token only ever exists in the emailed
        # link; if the database itself were ever exposed, this column
        # alone can't be used to take over an account.
        # Deadlines extracted from uploaded documents (syllabi, assignment sheets)
        cur.execute("""CREATE TABLE IF NOT EXISTS deadlines (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            course TEXT NOT NULL,
            title TEXT NOT NULL,
            due_date DATE,
            reminded BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW())""")
        # Saved chat conversations, so students can revisit and export past sessions
        cur.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            title TEXT DEFAULT 'New conversation',
            messages TEXT DEFAULT '[]',
            share_token TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW())""")
        # Foreign keys in Postgres do NOT automatically index the referencing
        # column, and these are exactly the columns get_docs(), log_event(),
        # and the analytics queries filter on for every single request.
        # Without these, those queries full-table-scan documents/events as
        # they grow — slower and more expensive DB CPU with every new
        # student and question.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_student_id ON documents(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_student_type ON events(student_id, event_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_student_id ON deadlines(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_due_date ON deadlines(due_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_conversations_student_id ON conversations(student_id)")
        # Chunked document text for retrieval (see services/retrieval.py).
        # student_id/university are denormalized from documents so a
        # retrieval query for "this student's chunks" or "this
        # university's global-reference chunks" doesn't need a join.
        cur.execute("""CREATE TABLE IF NOT EXISTS document_chunks (
            id SERIAL PRIMARY KEY,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            student_id INTEGER,
            university TEXT DEFAULT '',
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW())""")
        # Precomputed neural embedding for this chunk, stored as a JSON
        # float array — populated at upload time (once) if a neural
        # embedding backend is configured (see services/retrieval.py),
        # left NULL otherwise (TF-IDF needs no precomputed embedding at
        # all — see rank_chunks()). Storing it here means a question only
        # ever needs ONE new embedding call (the question itself), not one
        # per chunk, every single time.
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_student_id ON document_chunks(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_university ON document_chunks(university)")
        # Backs the shared, cross-worker rate limiter (see security.py).
        cur.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            id SERIAL PRIMARY KEY,
            key TEXT NOT NULL,
            ts TIMESTAMP DEFAULT NOW())""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rate_limits_key_ts ON rate_limits(key, ts)")
        # Spaced-repetition storage for /generate-practice's output. Each
        # row is one question; next_review_date/interval_days/streak track
        # this student's personal review schedule for it (see
        # services/practice.py's schedule_next_review()).
        cur.execute("""CREATE TABLE IF NOT EXISTS practice_questions (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            course TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            explanation TEXT DEFAULT '',
            interval_days INTEGER DEFAULT 1,
            correct_streak INTEGER DEFAULT 0,
            next_review_date DATE DEFAULT CURRENT_DATE,
            last_attempted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_practice_questions_student_id ON practice_questions(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_practice_questions_review_date ON practice_questions(student_id, next_review_date)")
        conn.commit(); cur.close()
        print("DB initialized OK.")
    except Exception as e:
        print(f"DB init error: {e}"); traceback.print_exc()
