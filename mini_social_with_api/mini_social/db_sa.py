import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")

def _normalize_database_url(url: str) -> str:
    # Render 給的 postgres URL 有時是 postgres://
    # SQLAlchemy 需要 postgresql://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url

if DATABASE_URL:
    DATABASE_URL = _normalize_database_url(DATABASE_URL)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    # 本機 fallback 用 SQLite
    engine = create_engine(
        "sqlite:///database.db",
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
