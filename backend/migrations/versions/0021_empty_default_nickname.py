# -*- coding: utf-8 -*-
"""0021 empty default nickname: clear the legacy default display name."""
import sqlalchemy as sa
from alembic import op

revision = "0021_empty_default_nickname"
down_revision = "0020_custom_plan_tasks"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy placeholder names are cleared so the UI shows "微信用户" until the
    # user actually sets a WeChat nickname.
    op.execute(
        sa.text("UPDATE users SET nickname = '' WHERE nickname = 'HealthMate 用户'")
    )


def downgrade():
    pass
