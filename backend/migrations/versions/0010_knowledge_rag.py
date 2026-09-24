"""Audited health knowledge documents for source-grounded RAG."""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0010_knowledge_rag"
down_revision = "0009_motion_coach"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("organization", sa.String(120), nullable=False),
        sa.Column("source_url", sa.String(700), nullable=False),
        sa.Column("source_published_at", sa.String(20), nullable=False),
        sa.Column("section", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_knowledge_documents_source_key", "knowledge_documents", ["source_key"])
    op.create_index("ix_knowledge_documents_organization", "knowledge_documents", ["organization"])
    op.create_index("ix_knowledge_documents_active", "knowledge_documents", ["active"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    documents = sa.table(
        "knowledge_documents",
        sa.column("source_key"), sa.column("title"), sa.column("organization"),
        sa.column("source_url"), sa.column("source_published_at"), sa.column("section"),
        sa.column("content"), sa.column("tags_json"), sa.column("active"),
        sa.column("reviewed_at"), sa.column("created_at"), sa.column("updated_at"),
    )
    op.bulk_insert(
        documents,
        [
            {
                "source_key": "who-pa-adults-2020",
                "title": "WHO 身体活动与久坐行为指南",
                "organization": "世界卫生组织（WHO）",
                "source_url": "https://www.who.int/publications/i/item/9789240015128",
                "source_published_at": "2020-11-25",
                "section": "18—64岁成年人",
                "content": "成年人每周宜进行至少150至300分钟中等强度有氧活动，或至少75至150分钟高强度有氧活动；还应每周至少2天进行覆盖主要肌群的力量活动。活动量应结合个人健康和能力循序增加。",
                "tags_json": '["运动指南","有氧","力量训练","每周运动","成年人"]',
                "active": True, "reviewed_at": now, "created_at": now, "updated_at": now,
            },
            {
                "source_key": "who-pa-general-2024",
                "title": "身体活动事实清单",
                "organization": "世界卫生组织（WHO）",
                "source_url": "https://www.who.int/news-room/fact-sheets/detail/physical-activity",
                "source_published_at": "2024-06-26",
                "section": "核心信息",
                "content": "规律身体活动有助于身心健康。任何活动都比完全不活动更好；应减少久坐，并依据年龄、健康状况和能力选择可持续的活动。",
                "tags_json": '["身体活动","久坐","运动益处","运动不足"]',
                "active": True, "reviewed_at": now, "created_at": now, "updated_at": now,
            },
            {
                "source_key": "nhc-dietary-guidelines-2022",
                "title": "中国居民膳食指南（2022）核心建议",
                "organization": "中华人民共和国国家卫生健康委员会",
                "source_url": "https://www.nhc.gov.cn/xcs/c100122/202206/d4941efa5d6544e2abac68127c3238c0.shtml",
                "source_published_at": "2022-06-27",
                "section": "发布会膳食指南介绍",
                "content": "日常饮食应保持食物多样、合理搭配，吃动平衡并保持健康体重；多吃蔬果、奶类、全谷和大豆，适量吃鱼禽蛋和瘦肉，并少盐、少油、控糖、限酒。",
                "tags_json": '["膳食指南","营养","平衡膳食","蔬菜水果","少盐少油","控糖"]',
                "active": True, "reviewed_at": now, "created_at": now, "updated_at": now,
            },
            {
                "source_key": "cdc-pa-chronic-conditions",
                "title": "慢性健康状况或残障成年人的身体活动指南",
                "organization": "美国疾病控制与预防中心（CDC）",
                "source_url": "https://www.cdc.gov/physical-activity-basics/guidelines/chronic-health-conditions-and-disabilities.html",
                "source_published_at": "2025-12-04",
                "section": "开始活动前的个体化考虑",
                "content": "有慢性健康状况或残障的成年人仍可从适当身体活动中获益；如果不能达到一般建议活动量，应在能力允许范围内保持活动，并向合格专业人员了解适合自己的活动类型和活动量。",
                "tags_json": '["慢病","高血压","糖尿病","残障","运动处方","咨询专业人员"]',
                "active": True, "reviewed_at": now, "created_at": now, "updated_at": now,
            },
        ],
    )


def downgrade():
    op.drop_table("knowledge_documents")
