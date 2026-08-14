"""add MFA columns to students for admin two-factor auth

Revision ID: 1900048daf40
Revises: 6535ed24cbc8
Create Date: 2026-08-14 21:45:42.817757

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '1900048daf40'
down_revision = '6535ed24cbc8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS mfa_secret TEXT")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS mfa_backup_codes TEXT DEFAULT '[]'")


def downgrade() -> None:
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS mfa_secret")
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS mfa_enabled")
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS mfa_backup_codes")
