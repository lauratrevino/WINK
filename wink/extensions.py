import logging

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
except ImportError:
    class _NoOpCSRF:
        def init_app(self, app):
            logger.warning(
                "flask-wtf is not installed in this environment, even "
                "though it's listed in requirements.txt. CSRF PROTECTION "
                "IS DISABLED. Trigger a clean rebuild of the deploy image "
                "(not just a restart) so pip actually reinstalls from the "
                "current requirements.txt, then redeploy."
            )
            app.jinja_env.globals.setdefault("csrf_token", lambda: "")

        def exempt(self, f):
            return f

    csrf = _NoOpCSRF()

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
        conn = _db_pool.getconn()
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


def init_app(app):
    csrf.init_app(app)

    @app.teardown_appcontext
    def _release_request_db_connection(exception=None):
        release_db()


def init_db():
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
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS preferred_language TEXT DEFAULT ''")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS terms_version TEXT")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS research_consent BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS research_consent_at TIMESTAMP")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS research_consent_version TEXT")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS account_deleted_at TIMESTAMP")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS anonymized_at TIMESTAMP")
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
