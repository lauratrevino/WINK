"""add university_other_name column to students

Revision ID: b7e3a9c1f502
Revises: f4b8c1e9a273
Create Date: 2026-09-05 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b7e3a9c1f502'
down_revision = 'f4b8c1e9a273'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, populated only when a student picks "Other" from the
    # university dropdown (see universities_list.py) — captures what
    # "Other" actually means for that student (e.g. a specific high
    # school, or a college not in the picklist) so chat personalization
    # and any future institution-specific logic has something more useful
    # than the literal string "Other" to work with. NULL for every
    # student who chose a real university, and for any "Other" account
    # created before this column existed.
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS university_other_name TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS university_other_name")
