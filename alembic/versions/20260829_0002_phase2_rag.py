"""Phase 2 RAG evaluation and evidence schema.

Revision ID: 20260829_0002
Revises: 20260829_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260829_0002"
down_revision: str | None = "20260829_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

corpus_status = sa.Enum("DRAFT", "READY", "INDEXING", "FAILED", "ARCHIVED", name="corpus_status")
document_source_type = sa.Enum("TEXT", "MARKDOWN", "PDF", name="document_source_type")
document_status = sa.Enum(
    "PENDING", "INDEXING", "READY", "FAILED", "ARCHIVED", name="document_status"
)
document_version_status = sa.Enum(
    "PENDING", "INDEXING", "READY", "FAILED", name="document_version_status"
)
trust_level = sa.Enum("TRUSTED", "STANDARD", "UNTRUSTED", name="trust_level")


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "corpora",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("status", corpus_status, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("project_id", "name", "version"),
    )
    op.create_index("ix_corpora_project_id", "corpora", ["project_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "corpus_id",
            sa.Uuid(),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_type", document_source_type, nullable=False),
        sa.Column("mime_type", sa.String(200), nullable=True),
        sa.Column("source_uri", sa.String(2048), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("status", document_status, nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("corpus_id", "name"),
    )
    op.create_index("ix_documents_corpus_id", "documents", ["corpus_id"])
    op.create_index("ix_documents_project_id", "documents", ["project_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trust_level", trust_level, nullable=False),
        sa.Column("status", document_version_status, nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", "version"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_content_hash", "document_versions", ["content_hash"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "corpus_id",
            sa.Uuid(),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(500), nullable=True),
        sa.Column("qdrant_point_id", sa.String(100), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_version_id", "chunk_index"),
        sa.UniqueConstraint("qdrant_point_id"),
    )
    op.create_index(
        "ix_document_chunks_scope",
        "document_chunks",
        ["organization_id", "project_id", "corpus_id"],
    )
    for field in (
        "document_version_id",
        "document_id",
        "corpus_id",
        "project_id",
        "organization_id",
    ):
        op.create_index(f"ix_document_chunks_{field}", "document_chunks", [field])

    op.create_table(
        "rag_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("embedding_provider", sa.String(100), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("dense_enabled", sa.Boolean(), nullable=False),
        sa.Column("sparse_enabled", sa.Boolean(), nullable=False),
        sa.Column("top_k_dense", sa.Integer(), nullable=False),
        sa.Column("top_k_sparse", sa.Integer(), nullable=False),
        sa.Column("hybrid_top_k", sa.Integer(), nullable=False),
        sa.Column("reranker_type", sa.String(100), nullable=False),
        sa.Column("reranker_model", sa.String(200), nullable=True),
        sa.Column("rerank_top_n", sa.Integer(), nullable=False),
        sa.Column("metadata_filter_policy", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("project_id", "name"),
    )
    op.create_index("ix_rag_configs_project_id", "rag_configs", ["project_id"])

    op.create_table(
        "gold_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Uuid(),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "chunk_id",
            sa.Uuid(),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("relevance_score", sa.Numeric(6, 4), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_gold_evidence_scenario_id", "gold_evidence", ["scenario_id"])


def downgrade() -> None:
    op.drop_table("gold_evidence")
    op.drop_table("rag_configs")
    op.drop_table("document_chunks")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("corpora")
    trust_level.drop(op.get_bind(), checkfirst=True)
    document_version_status.drop(op.get_bind(), checkfirst=True)
    document_status.drop(op.get_bind(), checkfirst=True)
    document_source_type.drop(op.get_bind(), checkfirst=True)
    corpus_status.drop(op.get_bind(), checkfirst=True)
