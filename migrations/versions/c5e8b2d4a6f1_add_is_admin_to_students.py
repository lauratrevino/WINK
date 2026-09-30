"""add is_admin column to students (admins excluded from usage statistics)

Revision ID: c5e8b2d4a6f1
Revises: a7c3e9d1b5f2
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op


revision = 'c5e8b2d4a6f1'
down_revision = 'a7c3e9d1b5f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Filled in from ADMIN_EMAILS by init_db() on every app startup.
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS is_admin")
