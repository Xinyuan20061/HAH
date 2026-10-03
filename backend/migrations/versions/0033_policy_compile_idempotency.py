"""persist user-level idempotency for policy compilation"""

from alembic import op
import sqlalchemy as sa


revision = "0033_policy_compile_idempotency"
down_revision = "0032_harness_plugins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("personal_strategy_units")}
    if "compile_idempotency_key" not in columns:
        op.add_column("personal_strategy_units", sa.Column("compile_idempotency_key", sa.String(length=120), nullable=True))
    if "compile_request_hash" not in columns:
        op.add_column("personal_strategy_units", sa.Column("compile_request_hash", sa.String(length=64), nullable=True))
    if "compile_response_json" not in columns:
        # MySQL rejects defaults on TEXT.  Add nullable, backfill, then tighten.
        op.add_column("personal_strategy_units", sa.Column("compile_response_json", sa.Text(), nullable=True))
        op.execute("UPDATE personal_strategy_units SET compile_response_json = '{}' WHERE compile_response_json IS NULL")
        if bind.dialect.name != "sqlite":
            op.alter_column("personal_strategy_units", "compile_response_json", existing_type=sa.Text(), nullable=False)
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("personal_strategy_units")}
    if "ix_personal_strategy_units_compile_idem" not in indexes:
        op.create_index("ix_personal_strategy_units_compile_idem", "personal_strategy_units", ["user_id", "compile_idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_personal_strategy_units_compile_idem", table_name="personal_strategy_units")
    op.drop_column("personal_strategy_units", "compile_response_json")
    op.drop_column("personal_strategy_units", "compile_request_hash")
    op.drop_column("personal_strategy_units", "compile_idempotency_key")
