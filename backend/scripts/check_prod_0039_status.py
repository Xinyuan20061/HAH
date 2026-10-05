"""Read-only production status check after an accidental migration attempt.

Queries alembic_version and whether the 0039 tables exist. Does NOT write.
"""

from sqlalchemy import create_engine, text

from app.core.config import settings

url = settings.effective_database_url.replace("%", "%%")
engine = create_engine(url)
with engine.connect() as conn:
    version = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    print("alembic_version:", version)
    for table in ("knowledge_claims", "knowledge_conflict_reviews", "knowledge_review_events"):
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table}).scalar()
        print(f"table {table}: exists={bool(exists)}")
        if exists:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            print(f"  row_count={count}")
engine.dispose()
