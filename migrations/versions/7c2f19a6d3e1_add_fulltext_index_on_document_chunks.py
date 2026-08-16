"""add full-text index on document_chunks.content for retrieval pre-filtering

Revision ID: 7c2f19a6d3e1
Revises: 1900048daf40
Create Date: 2026-08-16 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '7c2f19a6d3e1'
down_revision = '1900048daf40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # get_student_chunks() / get_global_chunks() (wink/services/documents.py)
    # used to pull EVERY chunk for a student (or every global chunk for a
    # university) into Python before ranking them against the question —
    # with no LIMIT and no way to narrow the candidate set at the database
    # level first. With the student document cap (20 docs) and per-document
    # extraction cap (60,000 chars, ~1000-char chunks), a single retrieval-
    # triggered chat message could pull thousands of chunks and their
    # embeddings into application memory before doing anything with them.
    #
    # This index backs a cheap server-side keyword pre-filter (ts_rank
    # against plainto_tsquery(question)) so the query itself can return
    # only the chunks that share vocabulary with the question, before a
    # hard LIMIT caps candidates regardless. The existing TF-IDF/neural
    # reranking in services/retrieval.py still does the actual final
    # ranking — this index only shrinks what has to be pulled into Python
    # to begin with.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_chunks_content_fts "
        "ON document_chunks USING GIN (to_tsvector('english', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_content_fts")
