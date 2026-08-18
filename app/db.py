from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate() -> None:
    """Лёгкие миграции для уже существующих БД (create_all новых колонок не добавит)."""
    with engine.connect() as conn:
        s_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(searches)"))}
        if "title_only" not in s_cols:
            conn.execute(
                text("ALTER TABLE searches ADD COLUMN title_only BOOLEAN NOT NULL DEFAULT 0")
            )
        if "resume_url" not in s_cols:
            conn.execute(text("ALTER TABLE searches ADD COLUMN resume_url TEXT"))
        if "ai_model" not in s_cols:
            conn.execute(text("ALTER TABLE searches ADD COLUMN ai_model VARCHAR(100)"))
        v_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(vacancies)"))}
        if "status" not in v_cols:
            conn.execute(text("ALTER TABLE vacancies ADD COLUMN status VARCHAR(30)"))
        if "applied_at" not in v_cols:
            conn.execute(text("ALTER TABLE vacancies ADD COLUMN applied_at DATETIME"))
        if "match_score" not in v_cols:
            conn.execute(text("ALTER TABLE vacancies ADD COLUMN match_score INTEGER"))
        conn.commit()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
