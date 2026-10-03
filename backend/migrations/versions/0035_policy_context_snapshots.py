"""freeze policy baseline/follow-up context and state snapshot hashes"""

from alembic import op
import sqlalchemy as sa


revision = "0035_policy_context_snapshots"
down_revision = "0034_source_record_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("policy_episodes", sa.Column("baseline_context_key", sa.String(length=64), nullable=True))
    op.add_column("policy_episodes", sa.Column("followup_context_key", sa.String(length=64), nullable=True))
    op.add_column("policy_episodes", sa.Column("baseline_state_snapshot_hash", sa.String(length=64), nullable=True))
    op.add_column("policy_episodes", sa.Column("followup_state_snapshot_hash", sa.String(length=64), nullable=True))
    op.add_column("policy_episodes", sa.Column("changed_variables_json", sa.Text(), nullable=True))
    op.execute("UPDATE policy_episodes SET changed_variables_json = '[]' WHERE changed_variables_json IS NULL")


def downgrade() -> None:
    for name in ("changed_variables_json", "followup_state_snapshot_hash", "baseline_state_snapshot_hash", "followup_context_key", "baseline_context_key"):
        op.drop_column("policy_episodes", name)
