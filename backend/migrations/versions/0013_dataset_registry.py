"""Audited registry of candidate research datasets; no raw data is bundled."""

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision = "0013_dataset_registry"
down_revision = "0012_training_semantics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dataset_registry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_key", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("homepage_url", sa.String(700), nullable=False),
        sa.Column("paper_url", sa.String(700), nullable=False),
        sa.Column("primary_use", sa.String(300), nullable=False),
        sa.Column("modalities_json", sa.Text(), nullable=False),
        sa.Column("access_mode", sa.String(50), nullable=False),
        sa.Column("license_summary", sa.Text(), nullable=False),
        sa.Column("redistribution_allowed", sa.Boolean(), nullable=False),
        sa.Column("adoption_status", sa.String(40), nullable=False),
        sa.Column("verified_on", sa.String(10), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_dataset_registry_dataset_key", "dataset_registry", ["dataset_key"])
    op.create_index("ix_dataset_registry_access_mode", "dataset_registry", ["access_mode"])
    op.create_index("ix_dataset_registry_adoption_status", "dataset_registry", ["adoption_status"])
    _seed_registry()


def _seed_registry():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    table = sa.table(
        "dataset_registry",
        sa.column("dataset_key"), sa.column("name"), sa.column("homepage_url"),
        sa.column("paper_url"), sa.column("primary_use"), sa.column("modalities_json"),
        sa.column("access_mode"), sa.column("license_summary"),
        sa.column("redistribution_allowed"), sa.column("adoption_status"),
        sa.column("verified_on"), sa.column("notes"),
        sa.column("created_at"), sa.column("updated_at"),
    )
    rows = [
        {
            "dataset_key": "mmfit", "name": "MM-Fit",
            "homepage_url": "https://mmfit.github.io/", "paper_url": "https://arxiv.org/abs/2003.10328",
            "primary_use": "多模态健身动作识别、计数和时序建模",
            "modalities": ["RGB-D", "2D/3D pose", "wearable sensors"], "access_mode": "public_site_review_required",
            "license_summary": "下载和使用前必须在官方页面复核当前许可证与再分发条款。",
            "adoption_status": "candidate", "notes": "优先用于运动场景预训练和协议验证。",
        },
        {
            "dataset_key": "fitness_aqa", "name": "Fitness-AQA",
            "homepage_url": "https://github.com/ParitoshParmar/Fitness-AQA", "paper_url": "https://arxiv.org/abs/2203.04851",
            "primary_use": "深蹲、推举和划船的细粒度动作质量评价",
            "modalities": ["RGB video", "quality/error labels"], "access_mode": "request_required",
            "license_summary": "官方仓库说明面向非商业研究且需要申请；使用前保留授权记录。",
            "adoption_status": "license_review", "notes": "用于AQA标签体系，不直接进入仓库。",
        },
        {
            "dataset_key": "ntu_rgbd_120", "name": "NTU RGB+D 120",
            "homepage_url": "https://rose1.ntu.edu.sg/dataset/actionRecognition/", "paper_url": "https://arxiv.org/abs/1905.04757",
            "primary_use": "大规模骨骼动作表示预训练",
            "modalities": ["RGB", "depth", "infrared", "3D skeleton"], "access_mode": "academic_request_required",
            "license_summary": "官方要求学术非商业申请，并限制数据再分发。",
            "adoption_status": "license_review", "notes": "不能将通用动作标签直接等同训练效果。",
        },
        {
            "dataset_key": "fitkg_cn", "name": "FitKG-CN",
            "homepage_url": "https://www.nature.com/articles/s41597-025-04519-6", "paper_url": "https://www.nature.com/articles/s41597-025-04519-6",
            "primary_use": "动作、身体部位、器械和训练目标知识关系",
            "modalities": ["knowledge graph"], "access_mode": "repository_license_check",
            "license_summary": "使用Zenodo数据前记录其具体许可证；导入关系仍需专家复审。",
            "adoption_status": "schema_reference", "notes": "当前只作为结构和候选关系参考。",
        },
        {
            "dataset_key": "flex", "name": "FLEX",
            "homepage_url": "https://haoyin116.github.io/FLEX_Dataset", "paper_url": "https://arxiv.org/abs/2506.03198",
            "primary_use": "运动视频、动作捕捉、sEMG和生理信号研究",
            "modalities": ["multi-view RGB", "3D mocap", "sEMG", "physiology"], "access_mode": "availability_check_required",
            "license_summary": "正式采用前核验数据开放范围、许可证和人体数据使用条件。",
            "adoption_status": "research_watch", "notes": "用于研究视频推断与实际肌肉信号的差距。",
        },
        {
            "dataset_key": "fitaqa_2026", "name": "FitAQA",
            "homepage_url": "https://arxiv.org/abs/2608.08736", "paper_url": "https://arxiv.org/abs/2608.08736",
            "primary_use": "多动作健身质量评价研究候选",
            "modalities": ["fitness video", "quality labels"], "access_mode": "paper_and_assets_check_required",
            "license_summary": "论文较新；数据、代码和许可证可得性必须在采用前重新核验。",
            "adoption_status": "research_watch", "notes": "不计入当前已实现能力。",
        },
    ]
    for row in rows:
        row["modalities_json"] = json.dumps(row.pop("modalities"), ensure_ascii=False)
        row["redistribution_allowed"] = False
        row["verified_on"] = "2026-09-21"
        row["created_at"] = now
        row["updated_at"] = now
    op.bulk_insert(table, rows)


def downgrade():
    op.drop_table("dataset_registry")
