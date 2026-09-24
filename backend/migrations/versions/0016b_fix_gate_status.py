"""Fix model_evaluations.gate_status length so 0017 seed fits MySQL.

Revision ID: 0016b_fix_gate_status
Revises: 0016_food_item_evidence
"""

from alembic import op


revision = "0016b_fix_gate_status"
down_revision = "0016_food_item_evidence"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite ignores VARCHAR length; only MySQL enforces it strictly.
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute(
            "ALTER TABLE model_evaluations MODIFY gate_status VARCHAR(80) NOT NULL"
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute(
            "ALTER TABLE model_evaluations MODIFY gate_status VARCHAR(30) NOT NULL"
        )
