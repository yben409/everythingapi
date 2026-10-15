import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./agentpay.db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def init_db(bind=engine) -> None:
    from app import ledger, webhooks  # noqa: F401  (registers the tables)

    Base.metadata.create_all(bind)


def get_session():
    with SessionLocal() as session:
        yield session
