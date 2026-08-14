"""add retrieved_context to answer_logs for research reproducibility

Revision ID: 6535ed24cbc8
Revises: a0205eeb64e6
Create Date: 2026-08-14 21:41:47.143371

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '6535ed24cbc8'
down_revision = 'a0205eeb64e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Captures the EXACT text (student document context + general
    # reference document context) that was actually included in the
    # prompt sent to the AI for this specific answer — a verbatim
    # snapshot at the moment of use, not just a reference to document
    # IDs. document_ids alone isn't enough for research reproducibility:
    # if the source document is later edited or deleted, "document 73
    # supported this answer" becomes unverifiable, since document 73's
    # content may no longer be what it was. This column makes what the
    # AI actually saw independently reconstructable regardless of what
    # happens to the source documents afterward.
    op.execute("ALTER TABLE answer_logs ADD COLUMN IF NOT EXISTS retrieved_context TEXT DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE answer_logs DROP COLUMN IF EXISTS retrieved_context")
