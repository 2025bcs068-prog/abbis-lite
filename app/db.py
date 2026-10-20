import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# SQLite by default for fast local dev; set DATABASE_URL=postgresql+psycopg://user:pass@localhost/abbis for Postgres
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///abbis.db")
engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)

class Base(DeclarativeBase):
    pass
