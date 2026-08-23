"""add student_id to demo_sessions

Revision ID: f4b8c1e9a273
Revises: e2a9f31c7d05
Create Date: 2026-08-23 00:00:00.000000

demo_sessions previously stored only aggregate stats (duration, question
count) with no link back to the actual student row. Now that expired/
ended demo accounts are kept (not hard-deleted — see wink/blueprints/
demo.py), this column lets Analytics jump from a demo_sessions row to
that account's real conversations/events for full transcript viewing.
Nullable and ON DELETE SET NULL: a demo_sessions row should outlive the
student row if it's ever removed some other way (e.g. the admin hard-
delete feature), rather than disappearing from the stats.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f4b8c1e9a273'
down_revision = 'e2a9f31c7d05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE demo_sessions ADD COLUMN IF NOT EXISTS student_id INTEGER REFERENCES students(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_demo_sessions_student_id ON demo_sessions(student_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_demo_sessions_student_id")
    op.execute("ALTER TABLE demo_sessions DROP COLUMN IF EXISTS student_id")
