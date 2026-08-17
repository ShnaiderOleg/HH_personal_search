import os
import pathlib
import tempfile

_tmp = tempfile.mkdtemp(prefix="hhsearch-tests-")
os.environ["DB_PATH"] = str(pathlib.Path(_tmp) / "test.db")
os.environ["POLL_INTERVAL_MINUTES"] = "999999"
os.environ["TG_BOT_TOKEN"] = ""
os.environ["TG_CHAT_IDS"] = ""
os.environ["SMTP_HOST"] = ""
os.environ["EMAIL_TO"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal, init_db
from app.models import Search
from app.main import app as fastapi_app


@pytest.fixture(scope="session", autouse=True)
def _prepare_db():
    init_db()
    yield


def _wipe():
    db = SessionLocal()
    try:
        for table in reversed(
            ["notification_log", "search_vacancies", "vacancies", "searches"]
        ):
            db.execute(text(f"DELETE FROM {table}"))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_db():
    _wipe()
    yield
    _wipe()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def make_search(db):
    def _make(**kwargs):
        defaults = {
            "title": "Тестовый поиск",
            "query": "python",
            "area_id": "1",
            "area_name": "Москва",
        }
        defaults.update(kwargs)
        search = Search(**defaults)
        db.add(search)
        db.commit()
        db.refresh(search)
        return search

    return _make


@pytest.fixture
def client():
    # Без контекстного менеджера lifespan (поллер) не запускается.
    return TestClient(fastapi_app)
