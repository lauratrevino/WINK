"""add campus_resources cache table

Revision ID: d8a1c47f0e91
Revises: b7e3a9c1f502
Create Date: 2026-09-06 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd8a1c47f0e91'
down_revision = 'b7e3a9c1f502'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Caches contact info (phone/email/office/hours) for the handful of
    # common campus support offices (Financial Aid, Counseling, Advising,
    # Writing Center, Tutoring, Career Center) per university, refreshed
    # periodically by services/campus_resources.py via a real Anthropic +
    # web_search call — the SAME lookup pipeline the chat itself would
    # otherwise run live, once per office instead of once per chat
    # message. See system_prompt.py for how this gets injected into the
    # chat system prompt so the model checks here before searching live.
    op.execute("""
        CREATE TABLE IF NOT EXISTS campus_resources (
            id SERIAL PRIMARY KEY,
            university TEXT NOT NULL,
            resource_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            contact_info TEXT NOT NULL DEFAULT '',
            source_urls TEXT DEFAULT '[]',
            refreshed_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(university, resource_key)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS campus_resources")
