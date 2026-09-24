"""Training intent, exercise-effect ontology, and motion semantic alignment."""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0012_training_semantics"
down_revision = "0011_motion_recognition"
branch_labels = None
depends_on = None

FITKG_URL = "https://www.nature.com/articles/s41597-025-04519-6"
ACSM_URL = "https://acsm.org/resistance-training-guidelines-update-2026/"


def upgrade():
    op.create_table(
        "fitness_concepts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("concept_key", sa.String(120), nullable=False, unique=True),
        sa.Column("concept_type", sa.String(40), nullable=False),
        sa.Column("name_zh", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("analyzer_support", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for name, columns in [
        ("ix_fitness_concepts_concept_key", ["concept_key"]),
        ("ix_fitness_concepts_concept_type", ["concept_type"]),
        ("ix_fitness_concepts_name_zh", ["name_zh"]),
        ("ix_fitness_concepts_analyzer_support", ["analyzer_support"]),
        ("ix_fitness_concepts_active", ["active"]),
    ]:
        op.create_index(name, "fitness_concepts", columns)

    op.create_table(
        "fitness_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_key",
            sa.String(120),
            sa.ForeignKey("fitness_concepts.concept_key"),
            nullable=False,
        ),
        sa.Column(
            "target_key",
            sa.String(120),
            sa.ForeignKey("fitness_concepts.concept_key"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(40), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(700), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "source_key", "target_key", "relation_type", name="uq_fitness_relation"
        ),
    )
    for name, columns in [
        ("ix_fitness_relations_source_key", ["source_key"]),
        ("ix_fitness_relations_target_key", ["target_key"]),
        ("ix_fitness_relations_relation_type", ["relation_type"]),
        ("ix_fitness_relations_review_status", ["review_status"]),
        ("ix_fitness_relations_active", ["active"]),
    ]:
        op.create_index(name, "fitness_relations", columns)

    op.create_table(
        "user_training_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("target_body_parts_json", sa.Text(), nullable=False),
        sa.Column("goals_json", sa.Text(), nullable=False),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("preferred_equipment_json", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_training_intents_user_id", "user_training_intents", ["user_id"])

    op.create_table(
        "motion_semantic_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("ai_jobs.id"), nullable=False, unique=True),
        sa.Column("exercise_key", sa.String(120), nullable=False),
        sa.Column("movement_patterns_json", sa.Text(), nullable=False),
        sa.Column("target_body_parts_json", sa.Text(), nullable=False),
        sa.Column("training_effects_json", sa.Text(), nullable=False),
        sa.Column("goal_alignment_json", sa.Text(), nullable=False),
        sa.Column("method", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_motion_semantic_analyses_user_id", "motion_semantic_analyses", ["user_id"])
    op.create_index("ix_motion_semantic_analyses_job_id", "motion_semantic_analyses", ["job_id"])
    op.create_index("ix_motion_semantic_analyses_exercise_key", "motion_semantic_analyses", ["exercise_key"])

    _seed_ontology()


def _seed_ontology():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    concepts = sa.table(
        "fitness_concepts",
        sa.column("concept_key"), sa.column("concept_type"), sa.column("name_zh"),
        sa.column("name_en"), sa.column("description"), sa.column("analyzer_support"),
        sa.column("active"), sa.column("created_at"), sa.column("updated_at"),
    )
    rows = []

    def concept(key, kind, zh, en="", description="", supported=False):
        rows.append({
            "concept_key": key, "concept_type": kind, "name_zh": zh,
            "name_en": en, "description": description,
            "analyzer_support": supported, "active": True,
            "created_at": now, "updated_at": now,
        })

    for key, zh, en, supported in [
        ("exercise:squat", "深蹲", "Squat", True),
        ("exercise:pushup", "俯卧撑", "Push-up", True),
        ("exercise:lunge", "弓步蹲", "Lunge", True),
        ("exercise:glute_bridge", "臀桥", "Glute bridge", False),
        ("exercise:plank", "平板支撑", "Plank", False),
        ("exercise:dumbbell_row", "哑铃划船", "Dumbbell row", False),
        ("exercise:overhead_press", "肩上推举", "Overhead press", False),
        ("exercise:biceps_curl", "二头弯举", "Biceps curl", False),
    ]:
        concept(key, "exercise", zh, en, "动作知识节点", supported)
    for key, zh, en in [
        ("body:chest", "胸部", "Chest"), ("body:back", "背部", "Back"),
        ("body:shoulders", "肩部", "Shoulders"), ("body:arms", "手臂", "Arms"),
        ("body:core", "核心", "Core"), ("body:quadriceps", "股四头肌", "Quadriceps"),
        ("body:glutes", "臀部", "Glutes"), ("body:hamstrings", "腘绳肌", "Hamstrings"),
    ]:
        concept(key, "body_part", zh, en)
    for key, zh in [
        ("pattern:squat", "蹲类模式"), ("pattern:lunge", "弓步/单侧下肢模式"),
        ("pattern:horizontal_push", "水平推"), ("pattern:horizontal_pull", "水平拉"),
        ("pattern:vertical_push", "垂直推"), ("pattern:elbow_flexion", "肘屈曲"),
        ("pattern:hip_extension", "髋伸展"), ("pattern:anti_extension", "核心抗伸展"),
    ]:
        concept(key, "movement_pattern", zh)
    for key, zh in [
        ("goal:strength", "力量"), ("goal:hypertrophy", "增肌"),
        ("goal:muscular_endurance", "肌耐力"), ("goal:balance", "平衡与稳定"),
        ("goal:core_stability", "核心稳定"),
    ]:
        concept(key, "training_goal", zh)
    op.bulk_insert(concepts, rows)

    relation_table = sa.table(
        "fitness_relations",
        sa.column("source_key"), sa.column("target_key"), sa.column("relation_type"),
        sa.column("role"), sa.column("weight"), sa.column("evidence"),
        sa.column("source_url"), sa.column("review_status"), sa.column("active"),
        sa.column("created_at"), sa.column("updated_at"),
    )
    relations = []

    def edge(exercise, target, relation, weight, role=""):
        source_url = ACSM_URL if relation == "supports_goal" else FITKG_URL
        relations.append({
            "source_key": f"exercise:{exercise}", "target_key": target,
            "relation_type": relation, "role": role, "weight": weight,
            "evidence": "比赛版初始知识边，正式发布前需运动专业人员逐条复审。",
            "source_url": source_url, "review_status": "seed",
            "active": True, "created_at": now, "updated_at": now,
        })

    targets = {
        "squat": [("body:quadriceps", .9, "primary"), ("body:glutes", .8, "primary"), ("body:hamstrings", .45, "secondary"), ("body:core", .4, "stabilizer")],
        "pushup": [("body:chest", .9, "primary"), ("body:arms", .75, "primary"), ("body:shoulders", .55, "secondary"), ("body:core", .45, "stabilizer")],
        "lunge": [("body:quadriceps", .85, "primary"), ("body:glutes", .8, "primary"), ("body:hamstrings", .5, "secondary"), ("body:core", .4, "stabilizer")],
        "glute_bridge": [("body:glutes", .95, "primary"), ("body:hamstrings", .55, "secondary"), ("body:core", .4, "stabilizer")],
        "plank": [("body:core", .95, "primary"), ("body:shoulders", .4, "stabilizer")],
        "dumbbell_row": [("body:back", .9, "primary"), ("body:arms", .6, "secondary"), ("body:core", .35, "stabilizer")],
        "overhead_press": [("body:shoulders", .9, "primary"), ("body:arms", .65, "secondary"), ("body:core", .4, "stabilizer")],
        "biceps_curl": [("body:arms", .95, "primary")],
    }
    for exercise, items in targets.items():
        for target, weight, role in items:
            edge(exercise, target, "targets", weight, role)
    patterns = {
        "squat": ["pattern:squat", "pattern:hip_extension"],
        "pushup": ["pattern:horizontal_push", "pattern:anti_extension"],
        "lunge": ["pattern:lunge", "pattern:hip_extension"],
        "glute_bridge": ["pattern:hip_extension"], "plank": ["pattern:anti_extension"],
        "dumbbell_row": ["pattern:horizontal_pull"],
        "overhead_press": ["pattern:vertical_push"],
        "biceps_curl": ["pattern:elbow_flexion"],
    }
    for exercise, items in patterns.items():
        for target in items:
            edge(exercise, target, "has_pattern", 1)
    goals = {
        "squat": ["goal:strength", "goal:hypertrophy", "goal:muscular_endurance"],
        "pushup": ["goal:strength", "goal:hypertrophy", "goal:muscular_endurance", "goal:core_stability"],
        "lunge": ["goal:strength", "goal:hypertrophy", "goal:balance"],
        "glute_bridge": ["goal:strength", "goal:hypertrophy"],
        "plank": ["goal:muscular_endurance", "goal:core_stability"],
        "dumbbell_row": ["goal:strength", "goal:hypertrophy"],
        "overhead_press": ["goal:strength", "goal:hypertrophy"],
        "biceps_curl": ["goal:strength", "goal:hypertrophy"],
    }
    for exercise, items in goals.items():
        for target in items:
            edge(exercise, target, "supports_goal", .7)
    op.bulk_insert(relation_table, relations)


def downgrade():
    op.drop_table("motion_semantic_analyses")
    op.drop_table("user_training_intents")
    op.drop_table("fitness_relations")
    op.drop_table("fitness_concepts")
