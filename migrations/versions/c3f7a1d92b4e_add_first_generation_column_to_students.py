"""add first_generation column to students

Revision ID: c3f7a1d92b4e
Revises: 9d4b7f2a1c88
Create Date: 2026-08-21 09:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c3f7a1d92b4e'
down_revision = '9d4b7f2a1c88'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS first_generation BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS first_generation")
