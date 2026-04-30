"""Postgres connection + schema bootstrap."""

import os
import time
import logging
from sqlalchemy import create_engine, Column, String, Integer, DateTime, func, text
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger("api.db")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://shortener:shortener@postgres:5432/shortener",
)

# pool_pre_ping handles transient drops; small pool_size keeps the
# failure mode realistic — under load you can exhaust connections,
# which is exactly the kind of thing we want AutoFixOps to detect.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
    pool_recycle=300,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Link(Base):
    __tablename__ = "links"
    code = Column(String(16), primary_key=True)
    target_url = Column(String, nullable=False)
    clicks = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


def init_db(retries: int = 30, delay: float = 1.0) -> None:
    """Wait for Postgres, then create tables."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(bind=engine)
            logger.info("Postgres ready (attempt %d).", attempt)
            return
        except Exception as e:
            last_err = e
            logger.warning("Postgres not ready (attempt %d/%d): %s", attempt, retries, e)
            time.sleep(delay)
    raise RuntimeError(f"Postgres unreachable after {retries} attempts: {last_err}")
