"""Motion coach scores, evidence events, and curated exercise resources."""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision = "0009_motion_coach"
down_revision = "0008_reliability"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "exercise_resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exercise_type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("url", sa.String(700), nullable=False, unique=True),
        sa.Column("difficulty", sa.String(20), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_exercise_resources_exercise_type", "exercise_resources", ["exercise_type"]
    )
    op.create_index(
        "ix_exercise_resources_platform", "exercise_resources", ["platform"]
    )
    op.create_index(
        "ix_exercise_resources_difficulty", "exercise_resources", ["difficulty"]
    )
    op.create_index("ix_exercise_resources_active", "exercise_resources", ["active"])

    op.create_table(
        "motion_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("ai_jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("exercise_type", sa.String(40), nullable=False),
        sa.Column("completeness", sa.Float(), nullable=False),
        sa.Column("stability", sa.Float(), nullable=False),
        sa.Column("rhythm_control", sa.Float(), nullable=False),
        sa.Column("risk_index", sa.Float(), nullable=False),
        sa.Column("overall", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_motion_scores_user_id", "motion_scores", ["user_id"])
    op.create_index("ix_motion_scores_job_id", "motion_scores", ["job_id"])
    op.create_index(
        "ix_motion_scores_exercise_type", "motion_scores", ["exercise_type"]
    )
    op.create_index("ix_motion_scores_overall", "motion_scores", ["overall"])

    op.create_table(
        "motion_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("ai_jobs.id"), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("timestamp_seconds", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "event_index", name="uq_motion_events_job_index"),
    )
    op.create_index("ix_motion_events_user_id", "motion_events", ["user_id"])
    op.create_index("ix_motion_events_job_id", "motion_events", ["job_id"])
    op.create_index("ix_motion_events_event_type", "motion_events", ["event_type"])
    op.create_index("ix_motion_events_severity", "motion_events", ["severity"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resources = sa.table(
        "exercise_resources",
        sa.column("exercise_type"),
        sa.column("title"),
        sa.column("platform"),
        sa.column("url"),
        sa.column("difficulty"),
        sa.column("tags_json"),
        sa.column("quality_score"),
        sa.column("summary"),
        sa.column("active"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    op.bulk_insert(
        resources,
        [
            {
                "exercise_type": "squat",
                "title": "7分钟深蹲跟练：入门到进阶详解",
                "platform": "B站",
                "url": "https://www.bilibili.com/video/BV1N5411b72P/",
                "difficulty": "beginner",
                "tags_json": '["标准动作","臀腿","跟练"]',
                "quality_score": 92.0,
                "summary": "从入门感觉到标准深蹲的循序练习。",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "exercise_type": "squat",
                "title": "深蹲与箭步蹲常见错误及膝部保护",
                "platform": "B站",
                "url": "https://www.bilibili.com/video/BV1y64y1z7i3/",
                "difficulty": "beginner",
                "tags_json": '["常见错误","膝部","纠正"]',
                "quality_score": 88.0,
                "summary": "讲解深蹲、箭步蹲的常见错误和一般训练注意事项。",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "exercise_type": "pushup",
                "title": "从0到1：标准俯卧撑入门与进阶",
                "platform": "B站",
                "url": "https://www.bilibili.com/video/BV1Ta411K72v/",
                "difficulty": "beginner",
                "tags_json": '["标准动作","入门","进阶"]',
                "quality_score": 94.0,
                "summary": "适合初学者建立完整俯卧撑动作。",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "exercise_type": "pushup",
                "title": "俯卧撑常见错误及正确做法",
                "platform": "B站",
                "url": "https://www.bilibili.com/video/BV1Ra4y177vh/",
                "difficulty": "intermediate",
                "tags_json": '["常见错误","身体直线","肩胛"]',
                "quality_score": 91.0,
                "summary": "覆盖身体姿势、动作幅度和肩胛控制。",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "exercise_type": "lunge",
                "title": "弓步蹲教学：膝盖与脚尖方向原则",
                "platform": "B站",
                "url": "https://www.bilibili.com/video/BV1rmcXeyExk/",
                "difficulty": "beginner",
                "tags_json": '["标准动作","膝盖方向","臀腿"]',
                "quality_score": 90.0,
                "summary": "讲解弓步蹲的原则性动作标准。",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade():
    op.drop_table("motion_events")
    op.drop_table("motion_scores")
    op.drop_table("exercise_resources")
