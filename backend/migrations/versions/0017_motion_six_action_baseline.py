"""Register the evaluated six-action rule baseline.

Revision ID: 0017_motion_six_action_baseline
Revises: 0016_food_item_evidence
"""

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision = "0017_motion_six_action_baseline"
down_revision = "0016b_fix_gate_status"
branch_labels = None
depends_on = None

MODEL_KEY = "motion-rule-recognizer"
VERSION = "2.0.0"
REPORT_SHA256 = "7bee63cc716b2d2835ec952fa0cc6017516fd95a45a9bf1b74e49fc0d9c4b575"
FOOD_REPORT_SHA256 = "fd3f52362fd85336cd055b0c6cf95932b3f4e0c16e8308b7d43222c70a8606ae"


def upgrade():
    connection = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    connection.execute(
        sa.text(
            "UPDATE model_registry SET active = :inactive, updated_at = :now "
            "WHERE model_key = :model_key"
        ),
        {"inactive": False, "now": now, "model_key": MODEL_KEY},
    )
    registry = sa.table(
        "model_registry",
        sa.column("model_key"),
        sa.column("version"),
        sa.column("task"),
        sa.column("implementation_type"),
        sa.column("release_status"),
        sa.column("artifact_uri"),
        sa.column("training_dataset_keys_json"),
        sa.column("label_schema_version"),
        sa.column("metrics_json"),
        sa.column("thresholds_json"),
        sa.column("code_revision"),
        sa.column("claims_scope"),
        sa.column("active"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    op.bulk_insert(
        registry,
        [
            {
                "model_key": MODEL_KEY,
                "version": VERSION,
                "task": "closed_set_motion_recognition",
                "implementation_type": "rule",
                "release_status": "evaluated_baseline",
                "artifact_uri": "ai-worker/healthmate_worker/processors/recognition.py",
                "training_dataset_keys_json": "[]",
                "label_schema_version": "motion-closed-v2-six-actions",
                "metrics_json": json.dumps(
                    {
                        "test_samples": 120,
                        "coverage_pct": 64.17,
                        "accuracy_all_samples_pct": 41.67,
                        "accuracy_accepted_pct": 64.94,
                        "macro_f1_pct": 54.15,
                    }
                ),
                "thresholds_json": json.dumps(
                    {
                        "min_match_score": 55,
                        "min_margin": 8,
                        "min_amplitude": {
                            "squat": 18,
                            "pushup": 18,
                            "lunge": 18,
                            "leg_abduction": 18,
                            "arm_abduction": 18,
                            "arm_vw": 18,
                        },
                    }
                ),
                "code_revision": "workspace-2026-09-23",
                "claims_scope": (
                    "六类规则识别工程基线；指标仅适用于登记的 REHAB24-6 固定测试集。"
                    "低准确率动作不得宣传为高精度，也不构成医学判断。"
                ),
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "model_key": "food-deepseek-vlm-baseline",
                "version": "1.0.0",
                "task": "food_nutrition_estimation",
                "implementation_type": "external_api",
                "release_status": "evaluated_baseline",
                "artifact_uri": "benchmark-results/food-v1/report.json",
                "training_dataset_keys_json": "[]",
                "label_schema_version": "food-result-v1",
                "metrics_json": json.dumps(
                    {
                        "test_samples": 42,
                        "completed_samples": 34,
                        "coverage_pct": 80.95,
                        "calorie_mae_kcal": 94.73,
                        "calorie_mape_pct": 72.70,
                    }
                ),
                "thresholds_json": "{}",
                "code_revision": "workspace-2026-09-23",
                "claims_scope": (
                    "Nutrition5k 42 图 VLM 基线，非最终精度；菜名使用食材代理标签，"
                    "不能当作标准菜品分类准确率。"
                ),
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "model_key": "motion-slowfast-r50",
                "version": "kinetics400-b62a501f",
                "task": "closed_set_motion_recognition",
                "implementation_type": "pretrained_candidate",
                "release_status": "not_finetuned",
                "artifact_uri": (
                    "D:/HealthMateData/motion/pretrained/"
                    "slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
                ),
                "training_dataset_keys_json": "[]",
                "label_schema_version": "kinetics400-upstream",
                "metrics_json": "{}",
                "thresholds_json": "{}",
                "code_revision": "workspace-2026-09-23",
                "claims_scope": (
                    "仅登记预训练初始化资源；尚未在六类 REHAB24-6 上微调或评测，"
                    "不得作为可用六分类模型。"
                ),
                "active": False,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    registry_id = connection.execute(
        sa.text(
            "SELECT id FROM model_registry WHERE model_key = :model_key AND version = :version"
        ),
        {"model_key": MODEL_KEY, "version": VERSION},
    ).scalar_one()
    evaluation = sa.table(
        "model_evaluations",
        sa.column("model_registry_id"),
        sa.column("dataset_key"),
        sa.column("split_name"),
        sa.column("report_sha256"),
        sa.column("sample_count"),
        sa.column("metrics_json"),
        sa.column("gate_status"),
        sa.column("evaluated_at"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    op.bulk_insert(
        evaluation,
        [
            {
                "model_registry_id": registry_id,
                "dataset_key": "rehab24-6-videos",
                "split_name": "fixed-test-subjects-7-8-9",
                "report_sha256": REPORT_SHA256,
                "sample_count": 120,
                "metrics_json": json.dumps(
                    {
                        "automatic_recognition": {
                            "coverage_pct": 64.17,
                            "accuracy_all_samples_pct": 41.67,
                            "accuracy_accepted_pct": 64.94,
                            "macro_f1_pct": 54.15,
                        },
                        "repetition_count": {
                            "accepted_sample_size": 55,
                            "mae_accepted": 0.5818,
                            "exact_accuracy_all_evaluable_pct": 19.17,
                        },
                    }
                ),
                "gate_status": "baseline_only_not_release_quality",
                "evaluated_at": now,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    food_registry_id = connection.execute(
        sa.text(
            "SELECT id FROM model_registry WHERE model_key = :model_key AND version = :version"
        ),
        {"model_key": "food-deepseek-vlm-baseline", "version": "1.0.0"},
    ).scalar_one()
    op.bulk_insert(
        evaluation,
        [
            {
                "model_registry_id": food_registry_id,
                "dataset_key": "nutrition5k-local-evaluation-subset",
                "split_name": "local-rgb-fixed-42",
                "report_sha256": FOOD_REPORT_SHA256,
                "sample_count": 42,
                "metrics_json": json.dumps(
                    {
                        "completed_samples": 34,
                        "coverage_pct": 80.95,
                        "calorie_mae_kcal": 94.73,
                        "calorie_mape_pct": 72.70,
                        "calorie_range_coverage_pct": 44.12,
                        "protein_mae_g": 9.48,
                        "carbs_mae_g": 7.35,
                        "fat_mae_g": 7.09,
                    }
                ),
                "gate_status": "baseline_only_prompt_or_classifier_revision_required",
                "evaluated_at": now,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade():
    connection = op.get_bind()
    for model_key, version in [
        (MODEL_KEY, VERSION),
        ("food-deepseek-vlm-baseline", "1.0.0"),
        ("motion-slowfast-r50", "kinetics400-b62a501f"),
    ]:
        registry_id = connection.execute(
            sa.text(
                "SELECT id FROM model_registry WHERE model_key = :model_key AND version = :version"
            ),
            {"model_key": model_key, "version": version},
        ).scalar()
        if registry_id is not None:
            connection.execute(
                sa.text("DELETE FROM model_evaluations WHERE model_registry_id = :registry_id"),
                {"registry_id": registry_id},
            )
            connection.execute(
                sa.text("DELETE FROM model_registry WHERE id = :registry_id"),
                {"registry_id": registry_id},
            )
    connection.execute(
        sa.text(
            "UPDATE model_registry SET active = :active "
            "WHERE model_key = :model_key AND version = :version"
        ),
        {"active": True, "model_key": MODEL_KEY, "version": "1.0.0"},
    )
