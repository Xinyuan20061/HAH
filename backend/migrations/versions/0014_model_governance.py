"""Observed pose semantics plus versioned model and evaluation governance."""

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision = "0014_model_governance"
down_revision = "0013_dataset_registry"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("motion_semantic_analyses") as batch:
        batch.add_column(sa.Column("observed_semantics_json", sa.Text(), nullable=True))
    op.execute("UPDATE motion_semantic_analyses SET observed_semantics_json = '{}' WHERE observed_semantics_json IS NULL")
    with op.batch_alter_table("motion_semantic_analyses") as batch:
        batch.alter_column("observed_semantics_json", existing_type=sa.Text(), nullable=False)
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_key", sa.String(100), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("task", sa.String(100), nullable=False),
        sa.Column("implementation_type", sa.String(30), nullable=False),
        sa.Column("release_status", sa.String(40), nullable=False),
        sa.Column("artifact_uri", sa.String(700), nullable=False),
        sa.Column("training_dataset_keys_json", sa.Text(), nullable=False),
        sa.Column("label_schema_version", sa.String(80), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("thresholds_json", sa.Text(), nullable=False),
        sa.Column("code_revision", sa.String(80), nullable=False),
        sa.Column("claims_scope", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("model_key", "version", name="uq_model_registry_version"),
    )
    for name, columns in [
        ("ix_model_registry_model_key", ["model_key"]),
        ("ix_model_registry_task", ["task"]),
        ("ix_model_registry_implementation_type", ["implementation_type"]),
        ("ix_model_registry_release_status", ["release_status"]),
        ("ix_model_registry_active", ["active"]),
    ]:
        op.create_index(name, "model_registry", columns)
    op.create_table(
        "model_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_registry_id", sa.Integer(), sa.ForeignKey("model_registry.id"), nullable=False),
        sa.Column("dataset_key", sa.String(80), nullable=False),
        sa.Column("split_name", sa.String(80), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("gate_status", sa.String(30), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for name, columns in [
        ("ix_model_evaluations_model_registry_id", ["model_registry_id"]),
        ("ix_model_evaluations_dataset_key", ["dataset_key"]),
        ("ix_model_evaluations_report_sha256", ["report_sha256"]),
        ("ix_model_evaluations_gate_status", ["gate_status"]),
    ]:
        op.create_index(name, "model_evaluations", columns)
    _seed_engineering_baselines()


def _seed_engineering_baselines():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    table = sa.table(
        "model_registry",
        sa.column("model_key"), sa.column("version"), sa.column("task"),
        sa.column("implementation_type"), sa.column("release_status"),
        sa.column("artifact_uri"), sa.column("training_dataset_keys_json"),
        sa.column("label_schema_version"), sa.column("metrics_json"),
        sa.column("thresholds_json"), sa.column("code_revision"),
        sa.column("claims_scope"), sa.column("active"),
        sa.column("created_at"), sa.column("updated_at"),
    )
    rows = [
        {
            "model_key": "motion-rule-recognizer", "version": "1.0.0",
            "task": "closed_set_motion_recognition", "implementation_type": "rule",
            "release_status": "engineering_baseline", "artifact_uri": "",
            "training_dataset_keys_json": "[]", "label_schema_version": "motion-closed-v1",
            "metrics_json": "{}",
            "thresholds_json": json.dumps({"min_match_score": 55, "min_margin": 8}),
            "code_revision": "", "claims_scope": "仅支持深蹲、俯卧撑、弓步蹲的规则匹配；无正式数据集准确率。",
        },
        {
            "model_key": "pose-compositional-semantics", "version": "1.0.0",
            "task": "compositional_motion_semantics", "implementation_type": "rule",
            "release_status": "engineering_baseline", "artifact_uri": "",
            "training_dataset_keys_json": "[]", "label_schema_version": "motion-semantic-v1",
            "metrics_json": "{}", "thresholds_json": json.dumps({"min_samples": 4}),
            "code_revision": "", "claims_scope": "输出可观察的体位、侧性、关节运动和身体区域；不代表动作名称或肌肉激活。",
        },
    ]
    for row in rows:
        row["active"] = True
        row["created_at"] = now
        row["updated_at"] = now
    op.bulk_insert(table, rows)


def downgrade():
    op.drop_table("model_evaluations")
    op.drop_table("model_registry")
    with op.batch_alter_table("motion_semantic_analyses") as batch:
        batch.drop_column("observed_semantics_json")
