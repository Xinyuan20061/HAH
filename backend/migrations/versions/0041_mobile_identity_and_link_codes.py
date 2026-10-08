"""Add provider-scoped identities for Android login and account linking.

Existing WeChat openids are backfilled under a temporary legacy issuer. The
first authenticated mini-program login after deployment upgrades that issuer to
the configured app id without creating a second user.
"""

from alembic import op
import sqlalchemy as sa


revision = "0041_mobile_identity_and_link_codes"
down_revision = "0040_evaluation_benchmark_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("issuer", sa.String(191), nullable=False),
        sa.Column("subject", sa.String(191), nullable=False),
        sa.Column("union_subject", sa.String(191), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "provider", "issuer", "subject", name="uq_user_identity_scope_subject"
        ),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])
    op.create_index(
        "ix_user_identities_user_provider",
        "user_identities",
        ["user_id", "provider"],
    )
    op.create_index(
        "ix_user_identities_provider_subject",
        "user_identities",
        ["provider", "subject"],
    )

    # `dev-user` is a development fixture identity, not a WeChat subject.
    op.execute(
        sa.text(
            """
            INSERT INTO user_identities
                (user_id, provider, issuer, subject, union_subject, created_at)
            SELECT id, 'wechat_miniprogram', 'legacy', openid, NULL, CURRENT_TIMESTAMP
            FROM users
            WHERE openid IS NOT NULL AND openid <> 'dev-user'
            """
        )
    )

    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "openid",
            existing_type=sa.String(length=128),
            existing_nullable=False,
            nullable=True,
        )

    op.create_table(
        "user_identity_link_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "source_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_user_identity_link_codes_source_user_id",
        "user_identity_link_codes",
        ["source_user_id"],
    )
    op.create_index(
        "ix_user_identity_link_codes_expires_at",
        "user_identity_link_codes",
        ["expires_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    null_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE openid IS NULL")
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            "Cannot downgrade while mobile-only users exist; migrate or remove those accounts first."
        )

    op.drop_index(
        "ix_user_identity_link_codes_expires_at",
        table_name="user_identity_link_codes",
    )
    op.drop_index(
        "ix_user_identity_link_codes_source_user_id",
        table_name="user_identity_link_codes",
    )
    op.drop_table("user_identity_link_codes")
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "openid",
            existing_type=sa.String(length=128),
            existing_nullable=True,
            nullable=False,
        )
    op.drop_index("ix_user_identities_provider_subject", table_name="user_identities")
    op.drop_index("ix_user_identities_user_provider", table_name="user_identities")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
