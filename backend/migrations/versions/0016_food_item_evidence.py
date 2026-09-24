"""Persist per-item food evidence for auditable nutrition estimates."""

from alembic import op
import sqlalchemy as sa


revision = "0016_food_item_evidence"
down_revision = "0015_skeleton_training_pipeline"
branch_labels = None
depends_on = None


def upgrade():
    # MySQL strict mode rejects DEFAULT on TEXT columns (SQLite allows it), so add
    # nullable first, backfill existing rows, then keep the column non-null in the
    # application layer (DietRecord.items handles None defensively).
    op.add_column(
        "diet_records",
        sa.Column("items_json", sa.Text(), nullable=True),
    )
    op.execute("UPDATE diet_records SET items_json = '[]' WHERE items_json IS NULL")


def downgrade():
    op.drop_column("diet_records", "items_json")
