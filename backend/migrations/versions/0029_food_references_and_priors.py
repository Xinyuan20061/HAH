"""0029 food references and priors.

Baseline (verified with `alembic heads` before authoring):
    head = 0028_motion_gold_metrics.

Capability plan §6 / §12. Additive-only:

  * ``food_references`` — the audited local nutrition table. Final nutrition is
    computed from here, never taken from a vision model's free-text numbers;
  * ``food_analysis_questions`` — the questions asked to shrink the estimate range,
    with the answers the user gave;
  * ``user_food_priors`` — personal portion priors, only after enough confirmations.

Nutrient values are stored per 100 g with the review provenance, because an
unreviewed number is exactly the "looks precise" failure the plan forbids.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_food_references_and_priors"
down_revision = "0028_motion_gold_metrics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "food_references",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("food_key", sa.String(length=80), nullable=False),
        sa.Column("name_zh", sa.String(length=120), nullable=False),
        sa.Column(
            "food_group", sa.String(length=40), nullable=False, server_default="other"
        ),
        sa.Column("calories_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("protein_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("carbs_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fat_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fiber_per_100g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("density_g_per_ml", sa.Float(), nullable=True),
        # No server_default on TEXT: MySQL forbids DEFAULT on TEXT/BLOB/JSON.
        sa.Column("aliases_json", sa.Text(), nullable=False),
        sa.Column("cooking_adjustments_json", sa.Text(), nullable=False),
        sa.Column("source_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("source_note", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("food_key", name="uq_food_reference_key"),
    )
    op.create_index("ix_food_references_food_group", "food_references", ["food_group"])
    op.create_index("ix_food_references_active", "food_references", ["active"])

    op.create_table(
        "food_analysis_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("food_analysis_sessions.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question_id", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("prompt", sa.String(length=300), nullable=False),
        sa.Column("options_json", sa.Text(), nullable=False),
        sa.Column("expected_range_reduction", sa.Float(), nullable=False, server_default="0"),
        sa.Column("answer_option_key", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "analysis_id", "question_id", name="uq_food_analysis_question"
        ),
    )
    op.create_index(
        "ix_food_analysis_questions_analysis_id",
        "food_analysis_questions",
        ["analysis_id"],
    )

    op.create_table(
        "user_food_priors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("food_key", sa.String(length=80), nullable=False),
        sa.Column(
            "context_key", sa.String(length=60), nullable=False, server_default="default"
        ),
        sa.Column("median_mass_g", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dispersion", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "food_key", "context_key", name="uq_user_food_prior"
        ),
    )
    op.create_index("ix_user_food_priors_user_id", "user_food_priors", ["user_id"])


def downgrade():
    op.drop_index("ix_user_food_priors_user_id", table_name="user_food_priors")
    op.drop_table("user_food_priors")

    op.drop_index(
        "ix_food_analysis_questions_analysis_id", table_name="food_analysis_questions"
    )
    op.drop_table("food_analysis_questions")

    op.drop_index("ix_food_references_active", table_name="food_references")
    op.drop_index("ix_food_references_food_group", table_name="food_references")
    op.drop_table("food_references")
