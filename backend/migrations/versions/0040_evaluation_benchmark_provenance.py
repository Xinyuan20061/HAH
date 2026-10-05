"""evaluation benchmark provenance: dataset / evidence level / retriever version"""

from alembic import op
import sqlalchemy as sa


revision = "0040_evaluation_benchmark_provenance"
down_revision = "0039_knowledge_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_benchmarks",
                  sa.Column("dataset", sa.String(120), nullable=False, server_default=""))
    op.add_column("evaluation_benchmarks",
                  sa.Column("evidence_level", sa.String(40), nullable=False, server_default=""))
    op.add_column("evaluation_benchmarks",
                  sa.Column("retriever_version", sa.String(60), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("evaluation_benchmarks", "retriever_version")
    op.drop_column("evaluation_benchmarks", "evidence_level")
    op.drop_column("evaluation_benchmarks", "dataset")
