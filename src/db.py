"""Database connection and migrations for LuBot Publisher."""

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://publisher:publisher_dev@localhost:5433/publisher")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_db():
    """Get a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_connection() -> bool:
    """Check if database connection is working."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        return result.scalar() == 1


def create_tables():
    """Create all tables from models."""
    Base.metadata.create_all(bind=engine)


def drop_tables():
    """Drop all tables. Use only in tests."""
    Base.metadata.drop_all(bind=engine)
