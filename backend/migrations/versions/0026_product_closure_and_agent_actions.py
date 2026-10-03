"""0026 product closure and agent actions.

Baseline (verified with `alembic heads` before authoring):
    head = 0025_motion_unified_v2_evidence (down = 0024_motion_voice_harness).

Additive-only migration per spec section 6.2 / 8.2 / 10.1:

  * ``diet_records.version`` — integer optimistic-concurrency counter, existing
    rows backfilled to 1 (no inferred meal type is written back).
  * ``ix_diet_records_user_recorded_id`` — cursor pagination key
    ``(user_id, recorded_at, id)``.
  * ``ck_diet_records_meal_type`` — the five frozen meal values, enforced in the
    database as well as the application layer.
  * new table ``health_agent_run_stages`` (durable run stage ledger).
  * new table ``agent_action_proposals`` (persist → confirm → execute → audit).
  * new table ``media_deletion_tasks`` (server-verifiable deletion ledger).

No historical revision is modified. New tables ship empty; no historical
proposal is replayed. ``downgrade`` only removes the columns/tables added here
and refuses nothing: a deployment that already wrote new-version rows must
export and stop writes first (documented in the runbook).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0026_product_closure_and_agent_actions"
down_revision = "0025_motion_unified_v2_evidence"
branch_labels = None
depends_on = None

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack", "other")


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    # --- alembic_version.version_num is VARCHAR(32) by default; the 0026
    # revision id ("0026_product_closure_and_agent_actions", 38 chars) exceeds
    # that length, so widen the column before the final version write.
    # MySQL only: SQLite has no fixed column length, so the ALTER is neither
    # needed nor valid there. The length guard keeps the ALTER idempotent.
    if bind.dialect.name == "mysql":
        av_cols = {c["name"]: c for c in insp.get_columns("alembic_version")}
        if "version_num" in av_cols:
            av_len = getattr(av_cols["version_num"]["type"], "length", 32) or 32
            if av_len < 64:
                op.execute(
                    "ALTER TABLE alembic_version MODIFY COLUMN version_num VARCHAR(64) NOT NULL"
                )
                insp = inspect(bind)

    table_names = set(insp.get_table_names())
    has_diet = "diet_records" in table_names
    dr_cols = {c["name"] for c in insp.get_columns("diet_records")} if has_diet else set()
    dr_indexes = (
        {i["name"] for i in insp.get_indexes("diet_records")} if has_diet else set()
    )
    dr_checks = (
        {ck["name"] for ck in insp.get_check_constraints("diet_records")}
        if has_diet
        else set()
    )

    # --- §6.2 diet record version + cursor index ---------------------------------
    # batch_alter_table is required for SQLite (ADD COLUMN + CHECK CONSTRAINT needs
    # a table rebuild); it is also valid on MySQL.
    # Idempotent: a prior partial/manual run may already have applied this block
    # (observed on the live DB: column+index+check present, alembic still at 0025).
    need_diet = (
        "version" not in dr_cols
        or "ix_diet_records_user_recorded_id" not in dr_indexes
        or "ck_diet_records_meal_type" not in dr_checks
    )
    if has_diet and need_diet:
        with op.batch_alter_table("diet_records") as batch:
            if "version" not in dr_cols:
                batch.add_column(
                    sa.Column("version", sa.Integer(), nullable=False, server_default="1")
                )
            if "ix_diet_records_user_recorded_id" not in dr_indexes:
                batch.create_index(
                    "ix_diet_records_user_recorded_id", ["user_id", "recorded_at", "id"]
                )
            if "ck_diet_records_meal_type" not in dr_checks:
                batch.create_check_constraint(
                    "ck_diet_records_meal_type",
                    "meal_type IN (" + ", ".join(f"'{value}'" for value in MEAL_TYPES) + ")",
                )

    # --- §8.2 durable agent run stages -------------------------------------------
    if "health_agent_run_stages" not in table_names:
        op.create_table(
            "health_agent_run_stages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("health_agent_runs.id"),
                nullable=False,
            ),
            sa.Column("stage_key", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("provider", sa.String(length=60), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            # No server_default: MySQL forbids a DEFAULT on TEXT/BLOB/JSON columns.
            # The application always writes this value explicitly.
            sa.Column("trace_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "run_id", "stage_key", "attempt", name="uq_agent_run_stage_attempt"
            ),
        )
        op.create_index(
            "ix_health_agent_run_stages_run_id", "health_agent_run_stages", ["run_id"]
        )
        op.create_index(
            "ix_health_agent_run_stages_status", "health_agent_run_stages", ["status"]
        )

    # --- §8.2 persistent action proposals ----------------------------------------
    if "agent_action_proposals" not in table_names:
        op.create_table(
            "agent_action_proposals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("proposal_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("health_agent_runs.id"),
                nullable=True,
            ),
            sa.Column("action_key", sa.String(length=80), nullable=False),
            sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="low"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            # MySQL forbids DEFAULT on TEXT; the application always supplies these.
            sa.Column("display_json", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=True),
            sa.Column(
                "audit_id",
                sa.Integer(),
                sa.ForeignKey("agent_action_audits.id"),
                nullable=True,
            ),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("proposal_id", name="uq_agent_action_proposal_id"),
        )
        op.create_index(
            "ix_agent_action_proposals_user_id", "agent_action_proposals", ["user_id"]
        )
        op.create_index(
            "ix_agent_action_proposals_run_id", "agent_action_proposals", ["run_id"]
        )
        op.create_index(
            "ix_agent_action_proposals_status", "agent_action_proposals", ["status"]
        )

    # --- §10.1 media deletion ledger ----------------------------------------------
    if "media_deletion_tasks" not in table_names:
        op.create_table(
            "media_deletion_tasks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_id", sa.String(length=64), nullable=False),
            # Intentionally NOT a foreign key: this ledger must survive the account
            # deletion it describes (spec §10.1 "账号删除后仍保留最小审计引用").
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("media_asset_id", sa.Integer(), nullable=True),
            sa.Column("storage_backend", sa.String(length=30), nullable=False, server_default=""),
            # Only a hash of the storage key survives account deletion: enough to
            # reconcile an orphaned object, never enough to fetch one.
            sa.Column("storage_key_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            # MySQL forbids DEFAULT on TEXT; the application always supplies a value.
            sa.Column("provider_receipt_json", sa.Text(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("task_id", name="uq_media_deletion_task_id"),
        )
        op.create_index(
            "ix_media_deletion_tasks_user_id", "media_deletion_tasks", ["user_id"]
        )
        op.create_index(
            "ix_media_deletion_tasks_status", "media_deletion_tasks", ["status"]
        )
        op.create_index(
            "ix_media_deletion_tasks_media_asset_id",
            "media_deletion_tasks",
            ["media_asset_id"],
        )
        op.create_index(
            "ix_media_deletion_tasks_next_attempt_at",
            "media_deletion_tasks",
            ["next_attempt_at"],
        )


def downgrade():
    op.drop_index(
        "ix_media_deletion_tasks_next_attempt_at", table_name="media_deletion_tasks"
    )
    op.drop_index(
        "ix_media_deletion_tasks_media_asset_id", table_name="media_deletion_tasks"
    )
    op.drop_index("ix_media_deletion_tasks_status", table_name="media_deletion_tasks")
    op.drop_index("ix_media_deletion_tasks_user_id", table_name="media_deletion_tasks")
    op.drop_table("media_deletion_tasks")

    op.drop_index(
        "ix_agent_action_proposals_status", table_name="agent_action_proposals"
    )
    op.drop_index(
        "ix_agent_action_proposals_run_id", table_name="agent_action_proposals"
    )
    op.drop_index(
        "ix_agent_action_proposals_user_id", table_name="agent_action_proposals"
    )
    op.drop_table("agent_action_proposals")

    op.drop_index(
        "ix_health_agent_run_stages_status", table_name="health_agent_run_stages"
    )
    op.drop_index(
        "ix_health_agent_run_stages_run_id", table_name="health_agent_run_stages"
    )
    op.drop_table("health_agent_run_stages")

    with op.batch_alter_table("diet_records") as batch:
        batch.drop_constraint("ck_diet_records_meal_type", type_="check")
        batch.drop_index("ix_diet_records_user_recorded_id")
        batch.drop_column("version")
