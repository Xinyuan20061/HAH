"""One-shot cleanup of 0039 leftovers accidentally created on production.

The evaluation script's alembic call hit production and partially ran 0039:
- knowledge_documents gained 5 audit columns
- knowledge_claims / knowledge_conflict_reviews were created (empty)
- knowledge_review_events was NOT created
- alembic_version stayed 0038 (0039 failed)

This script removes ONLY those leftovers, after verifying the tables are
empty. It does NOT touch alembic_version and does NOT roll back 0038 (which
applied cleanly and has no data migration).
"""

from sqlalchemy import create_engine, text

from app.core.config import settings

url = settings.effective_database_url.replace("%", "%%")
engine = create_engine(url)

COLS_TO_DROP = ["population", "exclusions", "reviewer", "review_expires_at", "content_sha256"]


def main() -> int:
    with engine.connect() as conn:
        for table in ("knowledge_claims", "knowledge_conflict_reviews"):
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table}).scalar()
            if not exists:
                print(f"skip {table}: does not exist")
                continue
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            if count != 0:
                print(f"ABORT: {table} has {count} rows; refusing to drop")
                return 1
        print("verified: leftover tables are empty")
        conn.execute(text("DROP TABLE IF EXISTS knowledge_conflict_reviews"))
        conn.execute(text("DROP TABLE IF EXISTS knowledge_claims"))
        for col in COLS_TO_DROP:
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'knowledge_documents' "
                "AND column_name = :c"), {"c": col}).scalar()
            if exists:
                conn.execute(text(f"ALTER TABLE knowledge_documents DROP COLUMN {col}"))
                print(f"dropped column: {col}")
            else:
                print(f"skip column {col}: not present")
        conn.commit()
    print("cleanup complete; alembic_version untouched (0038)")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
