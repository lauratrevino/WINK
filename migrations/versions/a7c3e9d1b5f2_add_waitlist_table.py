"""add waitlist table

Revision ID: a7c3e9d1b5f2
Revises: d8a1c47f0e91
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op


revision = 'a7c3e9d1b5f2'
down_revision = 'd8a1c47f0e91'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # People who tried to sign up after the Beta reached MAX_REGISTRATIONS.
    op.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            first_name TEXT DEFAULT '',
            university TEXT DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS waitlist")
