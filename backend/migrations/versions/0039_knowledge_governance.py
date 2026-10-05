"""knowledge governance: audit fields on reviewed chunks, claims, conflict reviews, review events"""

from alembic import op
import sqlalchemy as sa


revision = "0039_knowledge_governance"
down_revision = "0038_low_burden_evidence_acquisition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents",
                  sa.Column("population", sa.String(240), nullable=False, server_default=""))
    op.add_column("knowledge_documents",
                  sa.Column("exclusions", sa.String(240), nullable=False, server_default=""))
    op.add_column("knowledge_documents",
                  sa.Column("reviewer", sa.String(120), nullable=False, server_default=""))
    op.add_column("knowledge_documents",
                  sa.Column("review_expires_at", sa.DateTime(), nullable=True))
    op.add_column("knowledge_documents",
                  sa.Column("content_sha256", sa.String(64), nullable=False, server_default=""))

    op.create_table(
        "knowledge_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(120), sa.ForeignKey("knowledge_documents.source_key"), nullable=False),
        sa.Column("subject_population", sa.String(120), nullable=False, server_default=""),
        sa.Column("condition", sa.String(120), nullable=False, server_default=""),
        sa.Column("behavior", sa.String(160), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(160), nullable=False, server_default=""),
        sa.Column("direction", sa.String(32), nullable=False, server_default="unspecified"),
        sa.Column("strength", sa.String(32), nullable=False, server_default="unspecified"),
        sa.Column("qualifier", sa.String(240), nullable=False, server_default=""),
        sa.Column("source_location", sa.String(240), nullable=False, server_default=""),
        sa.Column("review_state", sa.String(40), nullable=False, server_default="reviewed"),
        sa.Column("reviewed_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("version_hash", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("claim_id", "version_hash", name="uq_knowledge_claim_version"),
        sa.CheckConstraint("direction IN ('increase','decrease','mixed','unspecified')",
                           name="ck_knowledge_claim_direction"),
        sa.CheckConstraint("review_state IN ('reviewed','retracted','expired','pending_review')",
                           name="ck_knowledge_claim_review_state"),
    )
    op.create_index("ix_knowledge_claims_source_key", "knowledge_claims", ["source_key"])
    op.create_index("ix_knowledge_claims_active", "knowledge_claims", ["active"])

    op.create_table(
        "knowledge_conflict_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_a_id", sa.Integer(), sa.ForeignKey("knowledge_claims.id"), nullable=False),
        sa.Column("claim_b_id", sa.Integer(), sa.ForeignKey("knowledge_claims.id"), nullable=False),
        sa.Column("conflict_status", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(240), nullable=False, server_default=""),
        sa.Column("resolution", sa.String(600), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "conflict_status IN ('not_assessed','no_conflict_found_in_reviewed_claims','potential_conflict','reviewed_resolved','insufficient_sources')",
            name="ck_knowledge_conflict_status",
        ),
        sa.CheckConstraint("claim_a_id <> claim_b_id", name="ck_knowledge_conflict_distinct"),
    )
    op.create_index("ix_knowledge_conflict_claims", "knowledge_conflict_reviews", ["claim_a_id", "claim_b_id"])

    op.create_table(
        "knowledge_review_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(120), sa.ForeignKey("knowledge_documents.source_key"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        # NOTE: TEXT/BLOB/JSON columns cannot carry a DEFAULT on MySQL; the
        # application layer writes payload_json explicitly.
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("event_type IN ('reviewed','revised','retracted','expired')",
                           name="ck_knowledge_review_event_type"),
    )
    op.create_index("ix_knowledge_review_events_source", "knowledge_review_events", ["source_key"])


def downgrade() -> None:
    op.drop_table("knowledge_review_events")
    op.drop_table("knowledge_conflict_reviews")
    op.drop_table("knowledge_claims")
    op.drop_column("knowledge_documents", "content_sha256")
    op.drop_column("knowledge_documents", "review_expires_at")
    op.drop_column("knowledge_documents", "reviewer")
    op.drop_column("knowledge_documents", "exclusions")
    op.drop_column("knowledge_documents", "population")
