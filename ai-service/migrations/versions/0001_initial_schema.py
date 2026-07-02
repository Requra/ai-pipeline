"""initial AI processing schema (jobs, chunks, embeddings, outputs, quality)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-01

Notes
-----
This initial migration provisions the whole durable schema in one step:

  1. Enables the ``vector`` (pgvector) extension.
  2. Creates every ai_* table + its indexes/constraints straight from the ORM
     metadata (``Base.metadata``). Using the metadata here — rather than
     hand-transcribing ~15 tables — guarantees the physical schema and the ORM
     never drift for the baseline. Subsequent, incremental migrations should be
     ``--autogenerate``d and reviewed as explicit ``op`` calls.
  3. Adds an IVFFLAT ANN index on the embedding column for cosine similarity
     (this is pgvector-specific and cannot be expressed in the ORM table args).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import settings
from app.store.db.models import Base

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # pgvector must exist before the embedding column's type is created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)

    # Approximate-nearest-neighbour index for cosine similarity search. lists is
    # a starting point; tune to ~sqrt(rows) for large corpora.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_embeddings_vec_cosine "
        "ON ai_source_chunk_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_ai_embeddings_vec_cosine")
    Base.metadata.drop_all(bind=bind)
