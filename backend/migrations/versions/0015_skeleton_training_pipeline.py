"""Register the trainable skeleton multi-label pipeline without claiming a model."""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0015_skeleton_training_pipeline"
down_revision = "0014_model_governance"
branch_labels = None
depends_on = None


def upgrade():
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
    op.bulk_insert(
        table,
        [
            {
                "model_key": "skeleton-stgcn-multilabel",
                "version": "pipeline-v1",
                "task": "compositional_motion_semantics",
                "implementation_type": "trainable_pipeline",
                "release_status": "not_trained",
                "artifact_uri": "",
                "training_dataset_keys_json": "[]",
                "label_schema_version": "motion-semantic-v1",
                "metrics_json": "{}",
                "thresholds_json": "{}",
                "code_revision": "",
                "claims_scope": "训练、阈值校准、TorchScript导出和安全回退链路已实现；尚无真实权重和正式测试集指标。",
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade():
    op.execute(
        "DELETE FROM model_registry "
        "WHERE model_key = 'skeleton-stgcn-multilabel' AND version = 'pipeline-v1'"
    )
