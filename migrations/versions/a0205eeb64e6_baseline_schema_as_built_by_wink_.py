"""baseline: schema as built by wink.extensions.init_db()

This migration exists to bring an already-running WINK database under
Alembic's management, not to build the schema from nothing. If you're
setting up a BRAND NEW database (a fresh dev environment, a new deploy
target), running `alembic upgrade head` will build the full schema from
scratch, correctly, starting from here.

If you're pointing this at your EXISTING production database (the one
that already has all these tables via wink.extensions.init_db(), which
still runs on every app startup and is untouched by this migration
system), do NOT run `alembic upgrade head` against it — that would try
to CREATE TABLE things that already exist. Instead run:

    alembic stamp head

That tells Alembic "this database already matches this migration,"
without trying to re-run any of the SQL below. From that point on, any
NEW schema change should be a NEW migration (`alembic revision -m "..."`)
layered on top of this one — not a new line added to init_db().

This migration was written by hand, transcribed directly from
wink/extensions.py's init_db() as it existed on 2026-08-14, and verified
by diffing the actual resulting Postgres schema (every table, column,
type, default, nullability, and index) against a database built by
running init_db() itself, on a byte-for-byte basis — not just reviewed
by eye. See the migrations/ README for how to re-run that verification
if this file is ever hand-edited later.

Revision ID: a0205eeb64e6
Revises:
Create Date: 2026-08-14
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a0205eeb64e6'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE students (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            classification TEXT NOT NULL,
            major TEXT NOT NULL,
            university TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE,
            email_verified BOOLEAN DEFAULT FALSE,
            verification_token TEXT,
            preferred_language TEXT DEFAULT '',
            terms_accepted_at TIMESTAMP,
            terms_version TEXT,
            research_consent BOOLEAN DEFAULT FALSE,
            research_consent_at TIMESTAMP,
            research_consent_version TEXT,
            account_deleted_at TIMESTAMP,
            anonymized_at TIMESTAMP,
            password_changed_at TIMESTAMP,
            is_demo BOOLEAN DEFAULT FALSE,
            demo_expires_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX idx_students_verification_token ON students(verification_token)")

    op.execute("""
        CREATE TABLE documents (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            orig_name TEXT NOT NULL,
            course TEXT NOT NULL,
            size_bytes INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            uploaded_at TIMESTAMP DEFAULT NOW(),
            crn TEXT DEFAULT '',
            university TEXT DEFAULT '',
            doc_type TEXT DEFAULT 'material',
            chunking_failed BOOLEAN DEFAULT FALSE
        )
    """)
    op.execute("CREATE INDEX idx_documents_global_university ON documents(university) WHERE student_id IS NULL")
    op.execute("CREATE INDEX idx_documents_student_id ON documents(student_id)")

    op.execute("""
        CREATE TABLE events (
            id SERIAL PRIMARY KEY,
            student_id INTEGER,
            event_type TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_events_student_type ON events(student_id, event_type)")
    op.execute("CREATE INDEX idx_events_type ON events(event_type)")
    op.execute("CREATE INDEX idx_events_created_at ON events(created_at)")

    op.execute("""
        CREATE TABLE password_resets (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE deadlines (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            course TEXT NOT NULL,
            title TEXT NOT NULL,
            due_date DATE,
            reminded BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            status TEXT DEFAULT 'detected',
            source_snippet TEXT DEFAULT '',
            confirmed_at TIMESTAMP,
            is_personal BOOLEAN NOT NULL DEFAULT FALSE,
            series_id TEXT,
            color TEXT,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            completed_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX idx_deadlines_status ON deadlines(status)")
    op.execute("CREATE INDEX idx_deadlines_student_id ON deadlines(student_id)")
    op.execute("CREATE INDEX idx_deadlines_due_date ON deadlines(due_date)")
    op.execute("CREATE INDEX idx_deadlines_document_id ON deadlines(document_id)")
    op.execute("CREATE INDEX idx_deadlines_series_id ON deadlines(series_id)")

    op.execute("""
        CREATE TABLE conversations (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            title TEXT DEFAULT 'New conversation',
            messages TEXT DEFAULT '[]',
            share_token TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX idx_conversations_student_id ON conversations(student_id)")

    op.execute("""
        CREATE TABLE document_chunks (
            id SERIAL PRIMARY KEY,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            student_id INTEGER,
            university TEXT DEFAULT '',
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            embedding TEXT
        )
    """)
    op.execute("CREATE INDEX idx_document_chunks_document_id ON document_chunks(document_id)")
    op.execute("CREATE INDEX idx_document_chunks_student_id ON document_chunks(student_id)")
    op.execute("CREATE INDEX idx_document_chunks_university ON document_chunks(university)")

    op.execute("""
        CREATE TABLE rate_limits (
            id SERIAL PRIMARY KEY,
            key TEXT NOT NULL,
            ts TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_rate_limits_key_ts ON rate_limits(key, ts)")

    op.execute("""
        CREATE TABLE practice_questions (
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
            created_at TIMESTAMP DEFAULT NOW(),
            qtype TEXT NOT NULL DEFAULT 'review',
            options TEXT,
            correct_index INTEGER
        )
    """)
    op.execute("CREATE INDEX idx_practice_questions_student_id ON practice_questions(student_id)")
    op.execute("CREATE INDEX idx_practice_questions_review_date ON practice_questions(student_id, next_review_date)")

    op.execute("""
        CREATE TABLE grading_weights (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            course TEXT NOT NULL,
            category TEXT NOT NULL,
            weight NUMERIC NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_grading_weights_student_course ON grading_weights(student_id, course)")

    op.execute("""
        CREATE TABLE answer_logs (
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
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_answer_logs_conv_msgidx ON answer_logs(conversation_id, message_index)")
    op.execute("CREATE INDEX idx_answer_logs_student_id ON answer_logs(student_id)")
    op.execute("CREATE INDEX idx_answer_logs_created_at ON answer_logs(created_at)")
    op.execute("CREATE INDEX idx_answer_logs_rating ON answer_logs(faculty_rating)")

    op.execute("""
        CREATE TABLE course_colors (
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
    op.execute("CREATE INDEX idx_course_colors_student ON course_colors(student_id)")

    op.execute("""
        CREATE TABLE token_usage (
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
    op.execute("CREATE INDEX idx_token_usage_student ON token_usage(student_id)")
    op.execute("CREATE INDEX idx_token_usage_created ON token_usage(created_at)")

    op.execute("""
        CREATE TABLE demo_sessions (
            id SERIAL PRIMARY KEY,
            started_at TIMESTAMP NOT NULL,
            ended_at TIMESTAMP NOT NULL DEFAULT NOW(),
            duration_seconds INTEGER NOT NULL,
            questions_asked INTEGER NOT NULL DEFAULT 0,
            ended_reason TEXT NOT NULL DEFAULT 'logout'
        )
    """)
    op.execute("CREATE INDEX idx_demo_sessions_ended_at ON demo_sessions(ended_at)")

    op.execute("""
        CREATE TABLE cron_runs (
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
    op.execute("CREATE INDEX idx_cron_runs_job_started ON cron_runs(job_name, started_at DESC)")

    op.execute("""
        CREATE TABLE email_suppressions (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE email_events (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail TEXT,
            raw_message_id TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_email_events_email ON email_events(email)")
    op.execute("CREATE INDEX idx_email_events_created_at ON email_events(created_at)")


def downgrade() -> None:
    # Destructive on purpose — this is the baseline, so "undo" means drop
    # everything. CASCADE handles the foreign-key dependency order so the
    # individual DROP order below doesn't have to be exact.
    for table in (
        "email_events", "email_suppressions", "cron_runs", "demo_sessions",
        "token_usage", "course_colors", "answer_logs", "grading_weights",
        "practice_questions", "rate_limits", "document_chunks",
        "conversations", "deadlines", "password_resets", "events",
        "documents", "students",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
