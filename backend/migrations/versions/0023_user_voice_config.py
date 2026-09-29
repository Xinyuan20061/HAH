"""Add encrypted per-user voice gateway configuration."""

import sqlalchemy as sa
from alembic import op


revision = "0023_user_voice_config"
down_revision = "0022_agent_decision_id"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_ai_configs",
        sa.Column("voice_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "user_ai_configs",
        sa.Column("voice_base_url", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "user_ai_configs",
        sa.Column("voice_stt_model", sa.String(length=120), nullable=False, server_default="whisper-1"),
    )
    op.add_column(
        "user_ai_configs",
        sa.Column("voice_tts_model", sa.String(length=120), nullable=False, server_default="tts-1"),
    )
    op.add_column(
        "user_ai_configs",
        sa.Column("voice_name", sa.String(length=80), nullable=False, server_default="alloy"),
    )
    # TEXT defaults are rejected by MySQL strict mode; NULL means no user override.
    op.add_column(
        "user_ai_configs",
        sa.Column("voice_api_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("user_ai_configs", "voice_api_key_encrypted")
    op.drop_column("user_ai_configs", "voice_name")
    op.drop_column("user_ai_configs", "voice_tts_model")
    op.drop_column("user_ai_configs", "voice_stt_model")
    op.drop_column("user_ai_configs", "voice_base_url")
    op.drop_column("user_ai_configs", "voice_enabled")
