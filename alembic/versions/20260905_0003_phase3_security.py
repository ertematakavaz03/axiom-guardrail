"""Add immutable project security policies and MCP inventory registrations.

Revision ID: 20260905_0003
Revises: 20260829_0002
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260905_0003"
down_revision = "20260829_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("policy", postgresql.JSONB(), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_security_policies_project_id", "security_policies", ["project_id"])
    op.create_table(
        "mcp_registrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("server", sa.String(200), nullable=False),
        sa.Column("inventory", postgresql.JSONB(), nullable=False),
        sa.Column("inventory_hash", sa.String(64), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_mcp_registrations_project_id", "mcp_registrations", ["project_id"])


def downgrade() -> None:
    op.drop_table("mcp_registrations")
    op.drop_table("security_policies")
