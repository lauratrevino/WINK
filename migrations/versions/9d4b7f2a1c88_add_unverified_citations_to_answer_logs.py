"""add unverified_citations to answer_logs for hallucinated-filename detection

Revision ID: 9d4b7f2a1c88
Revises: 7c2f19a6d3e1
Create Date: 2026-08-16 00:10:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '9d4b7f2a1c88'
down_revision = '7c2f19a6d3e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The chat frontend (templates/chat.html) already highlights any
    # filename-looking text the model writes in an answer, with an honest
    # tooltip caveat that this is "not an independently verified citation"
    # — it's a text match on the model's own output, not proof the cited
    # file actually supported the claim. This column adds one real,
    # checkable signal on top of that: whether each filename the model
    # named actually corresponds to a document that was shown to it at
    # all (the student's own uploads or the university's global reference
    # material), as opposed to a name the model invented outright. It
    # doesn't verify passage-level grounding — that would need a real
    # citation system tying claims to specific retrieved chunks — but it
    # does catch the more basic failure mode of citing a document that
    # was never in front of the model in the first place.
    op.execute("ALTER TABLE answer_logs ADD COLUMN IF NOT EXISTS unverified_citations TEXT DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE answer_logs DROP COLUMN IF EXISTS unverified_citations")
