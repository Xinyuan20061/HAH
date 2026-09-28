# -*- coding: utf-8 -*-
"""0022 agent decision_id: stable ledger key joining signal -> proposal ->
confirmation -> progress -> review for one micro experiment."""
import sqlalchemy as sa
from alembic import op

revision = "0022_agent_decision_id"
down_revision = "0021_empty_default_nickname"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_micro_experiments",
        sa.Column("decision_id", sa.String(length=64), nullable=True),
    )
    # Backfill legacy rows so every existing experiment has a stable ledger id.
    # Dialect-safe: SQLite uses || concatenation; MySQL requires CONCAT().
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute(
            sa.text(
                "UPDATE agent_micro_experiments "
                "SET decision_id = CONCAT('dec-', id) "
                "WHERE decision_id IS NULL OR decision_id = ''"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE agent_micro_experiments "
                "SET decision_id = 'dec-' || id "
                "WHERE decision_id IS NULL OR decision_id = ''"
            )
        )
    op.create_index(
        "ix_agent_micro_experiments_decision_id",
        "agent_micro_experiments",
        ["decision_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_agent_micro_experiments_decision_id",
        table_name="agent_micro_experiments",
    )
    op.drop_column("agent_micro_experiments", "decision_id")
