"""persist user decision snapshots idempotently"""

from alembic import op
import sqlalchemy as sa


revision = "0037_policy_decision_idempotency"
down_revision = "0036_harness_capability_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("policy_decisions", sa.Column("idempotency_key", sa.String(length=120), nullable=True))
    op.add_column("policy_decisions", sa.Column("idempotency_request_hash", sa.String(length=64), nullable=True))
    op.add_column("policy_decisions", sa.Column("candidate_set_hash", sa.String(length=64), nullable=True))
    op.add_column("policy_decisions", sa.Column("capability_snapshot_hash", sa.String(length=64), nullable=True))
    op.add_column("policy_decisions", sa.Column("response_json", sa.Text(), nullable=True))
    op.execute("UPDATE policy_decisions SET candidate_set_hash = '' WHERE candidate_set_hash IS NULL")
    op.execute("UPDATE policy_decisions SET capability_snapshot_hash = '' WHERE capability_snapshot_hash IS NULL")
    op.execute("UPDATE policy_decisions SET response_json = '{}' WHERE response_json IS NULL")
    with op.batch_alter_table("policy_decisions") as batch_op:
        batch_op.alter_column("candidate_set_hash", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column("capability_snapshot_hash", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column("response_json", existing_type=sa.Text(), nullable=False)
    op.create_index(
        "ix_policy_decisions_user_idempotency_key",
        "policy_decisions",
        ["user_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_policy_decisions_user_idempotency_key", table_name="policy_decisions")
    with op.batch_alter_table("policy_decisions") as batch_op:
        batch_op.drop_column("response_json")
        batch_op.drop_column("capability_snapshot_hash")
        batch_op.drop_column("candidate_set_hash")
        batch_op.drop_column("idempotency_request_hash")
        batch_op.drop_column("idempotency_key")
