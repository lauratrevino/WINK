"""add timezone column to students

Revision ID: e2a9f31c7d05
Revises: c3f7a1d92b4e
Create Date: 2026-08-22 04:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e2a9f31c7d05'
down_revision = 'c3f7a1d92b4e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no DB-level default on purpose: NULL means "we don't know
    # this student's real timezone yet" (never captured, or captured
    # before this column existed), and the application resolves that case
    # to config.APP_TIMEZONE itself (see resolve_student_timezone() in
    # wink/timeutil.py) rather than baking one fixed zone into the schema.
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS timezone TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS timezone")
