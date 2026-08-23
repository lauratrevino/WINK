import logging
import time
from contextlib import contextmanager

import httpx
import anthropic as ac
import psycopg2
from psycopg2 import pool as _pg_pool
from psycopg2.extras import RealDictCursor
from flask import g

from . import config

logger = logging.getLogger(__name__)

try:
    from flask_wtf import CSRFProtect
    csrf = CSRFProtect()
except ImportError as e:
    # Previously fell back to a no-op CSRFProtect stand-in and kept running
    # with CSRF protection silently disabled — fine for surfacing the
    # problem in logs, but the wrong default for anything handling real
    # student accounts: a missing dependency should never quietly downgrade
    # security posture in production. Fail closed instead: refuse to boot
    # at all until the dependency is actually present, the same way a
    # missing SECRET_KEY or DB_URL would be treated as fatal elsewhere in
    # this app. flask-wtf is pinned in requirements.txt, so this should
    # only ever fire from a broken/incomplete build image — trigger a
    # clean rebuild (not just a restart) so pip actually reinstalls from
    # the current requirements.txt.
    logger.critical(
        "flask-wtf is not installed in this environment, even though "
        "it's listed in requirements.txt. Refusing to start rather than "
        "run with CSRF protection disabled. Trigger a clean rebuild of "
        "the deploy image (not just a restart) so pip actually "
        "reinstalls from the current requirements.txt, then redeploy."
    )
    raise


def generate_csrf_token():
    """For the handful of plain-string (non-Jinja) fallback error pages
    across the blueprints, which can't use the `csrf_token()` Jinja global
    but still render a real <form method='POST' action='/logout'> — that
    form needs a real token now that /logout is no longer CSRF-exempt.
    flask-wtf is guaranteed importable here: the module-level import above
    already raises (and refuses to start the app) if it's missing, so
    there's no silent "" fallback to keep in sync with anymore."""
    from flask_wtf.csrf import generate_csrf
    return generate_csrf()

_http_client = httpx.Client(
    timeout=httpx.Timeout(110.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
    http2=True,
)
anthropic_client = (
    ac.Anthropic(api_key=config.ANTHROPIC_API_KEY, http_client=_http_client)
    if config.ANTHROPIC_API_KEY else None
)

voyage_client = None
if config.VOYAGE_API_KEY:
    try:
        import voyageai
        voyage_client = voyageai.Client(api_key=config.VOYAGE_API_KEY)
    except ImportError:
        logger.warning(
            "VOYAGE_API_KEY is set but the voyageai package isn't installed — "
            "falling back to TF-IDF retrieval. Add voyageai to requirements.txt "
            "and redeploy to enable neural embeddings."
        )

_db_pool = None
if config.DB_URL:
    try:
        _db_pool = _pg_pool.ThreadedConnectionPool(
            config.DB_POOL_MIN, config.DB_POOL_MAX, config.DB_URL,
            cursor_factory=RealDictCursor
        )
    except Exception:
        logger.warning("DB pool init failed, falling back to per-request connections", exc_info=True)
        _db_pool = None


def get_db():
    if getattr(g, "_db_conn", None) is not None:
        return g._db_conn
    if _db_pool:
        # Under bursty concurrent load (many students chatting at once, each
        # holding a connection for their pre-stream DB work), the pool can be
        # momentarily fully checked out even though it's sized reasonably —
        # other requests finish and call release_db() within milliseconds.
        # getconn() raises PoolError immediately with no wait, so without
        # this retry, a request arriving during that brief window fails
        # outright instead of getting a connection a few dozen milliseconds
        # later. This does NOT mask genuine, sustained exhaustion (a pool
        # sized far too small, a stuck/leaked connection) — after ~0.5s of
        # retrying, it still raises, same as before, just no longer on every
        # momentary blip.
        conn = None
        last_err = None
        for attempt in range(5):
            try:
                conn = _db_pool.getconn()
                break
            except _pg_pool.PoolError as e:
                last_err = e
                if attempt < 4:
                    time.sleep(0.05 * (attempt + 1))
        if conn is None:
            raise last_err
    else:
        conn = psycopg2.connect(config.DB_URL, cursor_factory=RealDictCursor)
    g._db_conn = conn
    return conn


def release_db():
    """Explicitly returns the current request's DB connection to the pool
    (or closes it, if there's no pool) without waiting for the request to
    fully end. Safe to call even if no connection was ever acquired; a
    later get_db() call in the same request transparently opens a new one.

    This matters specifically for streaming responses (see /chat): the
    normal teardown_appcontext release below only fires once the ENTIRE
    response — including the full streamed body — has finished sending.
    Without this, a connection acquired for pre-stream DB reads (loading
    documents, the conversation row, etc.) sits checked out of the pool,
    doing nothing, for the many-seconds duration of the AI response
    streaming out to the browser. With many students chatting at once,
    that alone is enough to exhaust the pool and start failing new
    requests — even though the database itself is barely being used
    during that window. Call this right before starting a long-running
    stream, once any DB work needed to prepare it is done.
    """
    conn = g.pop("_db_conn", None)
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        pass
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


@contextmanager
def db_cursor(commit=False):
    """Yields a cursor on the current request's shared connection (the
    same one get_db() returns — reused across the whole request via
    Flask's `g`), always closing the cursor on the way out, including on
    an exception. Replaces the `conn = get_db(); cur = conn.cursor()` /
    `cur.close()` pair that used to be retyped at every call site, and
    removes the chance of a call site forgetting the close.

    Pass commit=True for anything that writes; the connection commits
    once the block finishes without raising. On an exception, the block
    exits without committing — the caller's own error handling (if any)
    still runs after that, on an uncommitted transaction. Read-only call
    sites should leave commit=False (the default) so a query never
    triggers a needless commit.

        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE ... WHERE id=%s", (item_id,))
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    finally:
        cur.close()


def init_app(app):
    csrf.init_app(app)

    @app.teardown_appcontext
    def _release_request_db_connection(exception=None):
        release_db()


def init_db():
    """Builds/updates the schema this app expects, idempotently — safe to
    run on every startup (every statement is CREATE ... IF NOT EXISTS or
    ADD COLUMN IF NOT EXISTS). This keeps running on every deploy exactly
    as before; it is NOT being replaced or removed by the Alembic setup
    in migrations/.

    Going forward, do NOT add new schema changes as new lines in this
    function — that was fine for a while, but it's exactly what let a
    real ordering bug hide here for a long time (an index on a column
    that isn't added until 80 lines later — worked on every database
    that already had the column, would have failed outright on a
    genuinely fresh one; caught and fixed via the Alembic baseline
    verification in migrations/versions/a0205eeb64e6_..._baseline_...py).

    Any NEW schema change from here on should be a new Alembic migration
    (`alembic revision -m "..."`, write upgrade()/downgrade(), then
    `alembic upgrade head`) — see migrations/README.md. This function
    stays as-is, frozen at what it already builds, purely so existing
    deploys keep working without any extra manual step."""
    if not config.DB_URL:
        logger.warning("No DATABASE_URL set.")
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
        # Queried on every single email-verification click (WHERE
        # verification_token=%s in auth.py) with no other filter — without
        # this, that's a full table scan across every student for a
        # routine, frequent action.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_students_verification_token ON students(verification_token)")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS preferred_language TEXT DEFAULT ''")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS terms_version TEXT")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS research_consent BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS research_consent_at TIMESTAMP")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS research_consent_version TEXT")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS account_deleted_at TIMESTAMP")
        # first_generation was originally added only via Alembic migration
        # c3f7a1d92b4e (see migrations/versions/) — mirrored here too so a
        # fresh database relying on init_db() alone (a new test DB, a fresh
        # local dev setup) doesn't break on registration with
        # "column first_generation does not exist." Both paths are
        # idempotent, so running both is safe.
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS first_generation BOOLEAN NOT NULL DEFAULT FALSE")
        # Same drift as first_generation above — timezone was only ever
        # added via Alembic migration e2a9f31c7d05. Nullable, no default,
        # matching that migration exactly: NULL means "we don't know this
        # student's real timezone yet," resolved to config.APP_TIMEZONE by
        # resolve_student_timezone() in wink/timeutil.py rather than baked
        # into the schema.
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS timezone TEXT")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS anonymized_at TIMESTAMP")
        # Lets current_student() detect and invalidate sessions issued
        # before the most recent password change — without this, resetting
        # a password (e.g. after a suspected compromise) doesn't actually
        # revoke any session that was already logged in with the old one.
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP")
        # Admin authorization is otherwise just "does the logged-in
        # email match ADMIN_EMAIL" — a single password is the entire
        # barrier between anyone and full access to (anonymized, but
        # still real) student research data. MFA closes the specific
        # gap that matters: if that one password is ever phished,
        # guessed, or reused from a breached site, this is the second
        # factor standing in the way.
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS mfa_secret TEXT")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS mfa_backup_codes TEXT DEFAULT '[]'")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS is_demo BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS demo_expires_at TIMESTAMP")
        cur.execute("""CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, orig_name TEXT NOT NULL,
            course TEXT NOT NULL, size_bytes INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            uploaded_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT DEFAULT ''")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS crn TEXT DEFAULT ''")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS university TEXT DEFAULT ''")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type TEXT DEFAULT 'material'")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunking_failed BOOLEAN DEFAULT FALSE")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_global_university ON documents(university) WHERE student_id IS NULL")
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
        cur.execute("""CREATE TABLE IF NOT EXISTS deadlines (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            course TEXT NOT NULL,
            title TEXT NOT NULL,
            due_date DATE,
            reminded BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'detected'")
        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS source_snippet TEXT DEFAULT ''")
        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMP")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_status ON deadlines(status)")
        cur.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            title TEXT DEFAULT 'New conversation',
            messages TEXT DEFAULT '[]',
            share_token TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_student_id ON documents(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_student_type ON events(student_id, event_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_student_id ON deadlines(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_due_date ON deadlines(due_date)")
        # document_id has no student_id filter on its own queries (deadline
        # cleanup on document reupload/reprocess deletes by document_id
        # alone), so without this index those deletes scan every student's
        # deadlines, not just one student's.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_document_id ON deadlines(document_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_conversations_student_id ON conversations(student_id)")
        cur.execute("""CREATE TABLE IF NOT EXISTS document_chunks (
            id SERIAL PRIMARY KEY,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            student_id INTEGER,
            university TEXT DEFAULT '',
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_student_id ON document_chunks(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_university ON document_chunks(university)")
        # Backs the keyword pre-filter in get_student_chunks()/get_global_chunks()
        # (services/documents.py) — see migration 7c2f19a6d3e1 for the full
        # rationale (retrieval used to load every chunk into Python with no
        # candidate reduction at the database level first).
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_chunks_content_fts "
            "ON document_chunks USING GIN (to_tsvector('english', content))"
        )
        cur.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            id SERIAL PRIMARY KEY,
            key TEXT NOT NULL,
            ts TIMESTAMP DEFAULT NOW())""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rate_limits_key_ts ON rate_limits(key, ts)")
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

        cur.execute("""CREATE TABLE IF NOT EXISTS grading_weights (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            course TEXT NOT NULL,
            category TEXT NOT NULL,
            weight NUMERIC NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_grading_weights_student_course ON grading_weights(student_id, course)")

        cur.execute("""CREATE TABLE IF NOT EXISTS answer_logs (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            conversation_id INTEGER,
            message_index INTEGER,
            question TEXT NOT NULL,
            answer_text TEXT DEFAULT '',
            model TEXT NOT NULL,
            retrieval_backend TEXT NOT NULL,
            chunk_count INTEGER DEFAULT 0,
            document_ids TEXT DEFAULT '[]',
            latency_ms INTEGER,
            prompt_version TEXT DEFAULT 'v1',
            student_feedback TEXT,
            faculty_rating TEXT,
            faculty_notes TEXT DEFAULT '',
            rated_by TEXT DEFAULT '',
            rated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_answer_logs_conv_msgidx ON answer_logs(conversation_id, message_index)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_answer_logs_student_id ON answer_logs(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_answer_logs_created_at ON answer_logs(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_answer_logs_rating ON answer_logs(faculty_rating)")
        # Verbatim snapshot of what the AI actually saw for this answer
        # (the exact retrieved document context, not just document IDs) —
        # see migrations/versions/6535ed24cbc8_... for the full reasoning.
        cur.execute("ALTER TABLE answer_logs ADD COLUMN IF NOT EXISTS retrieved_context TEXT DEFAULT ''")
        # Filenames the model named in its answer that don't correspond to
        # any document actually shown to it (student uploads or global
        # reference material) — see migration 9d4b7f2a1c88 for the full
        # reasoning. Empty string means either no filenames were mentioned,
        # or every one mentioned was real.
        cur.execute("ALTER TABLE answer_logs ADD COLUMN IF NOT EXISTS unverified_citations TEXT DEFAULT ''")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS course_colors (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                course_normalized TEXT NOT NULL,
                course_display TEXT NOT NULL,
                color TEXT NOT NULL,
                assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (student_id, course_normalized),
                UNIQUE (student_id, color)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_course_colors_student ON course_colors(student_id)")

        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS is_personal BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS series_id TEXT")
        # Moved here from much earlier in this function — it was
        # previously creating this index before this column existed on a
        # genuinely fresh database (only ever masked because every real
        # database in use already had the column from an earlier point in
        # this file's history; caught via Alembic baseline verification).
        # series_id lookups are already scoped by student_id (which is
        # indexed), so this mostly helps the "apply to whole series" UPDATE
        # once a student is already narrowed down — cheap to add, no
        # meaningful write-side cost.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_series_id ON deadlines(series_id)")
        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS color TEXT")
        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS completed BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute("ALTER TABLE deadlines ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")

        cur.execute("ALTER TABLE practice_questions ADD COLUMN IF NOT EXISTS qtype TEXT NOT NULL DEFAULT 'review'")
        cur.execute("ALTER TABLE practice_questions ADD COLUMN IF NOT EXISTS options TEXT")
        cur.execute("ALTER TABLE practice_questions ADD COLUMN IF NOT EXISTS correct_index INTEGER")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                call_type TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_student ON token_usage(student_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS demo_sessions (
                id SERIAL PRIMARY KEY,
                started_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP NOT NULL DEFAULT NOW(),
                duration_seconds INTEGER NOT NULL,
                questions_asked INTEGER NOT NULL DEFAULT 0,
                ended_reason TEXT NOT NULL DEFAULT 'logout'
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_demo_sessions_ended_at ON demo_sessions(ended_at)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cron_runs (
                id SERIAL PRIMARY KEY,
                job_name TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMP,
                number_processed INTEGER NOT NULL DEFAULT 0,
                number_sent INTEGER NOT NULL DEFAULT 0,
                number_failed INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cron_runs_job_started ON cron_runs(job_name, started_at DESC)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_suppressions (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_events (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT,
                raw_message_id TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_email_events_email ON email_events(email)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_email_events_created_at ON email_events(created_at)")

        conn.commit(); cur.close()
        logger.info("DB initialized OK.")
    except Exception as e:
        logger.error("DB init error", exc_info=True)
        raise RuntimeError(
            "Database schema initialization failed — refusing to start. "
            "Serving traffic against a broken/partial schema is worse than "
            "not starting at all. See the exception above for the actual "
            "migration failure."
        ) from e
