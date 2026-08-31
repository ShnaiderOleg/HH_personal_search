from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from app.models import Vacancy, utcnow


def test_pages_use_versioned_static_urls(client):
    from app.routers.pages import templates

    static_version = templates.env.globals["static_version"]
    assert static_version

    for path in ("/", "/searches", "/vacancies"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert f'/static/style.css?v={static_version}' in resp.text
        assert f'/static/app.js?v={static_version}' in resp.text


def test_static_version_can_be_configured(monkeypatch):
    from app.routers import pages

    monkeypatch.setattr(
        pages,
        "get_settings",
        lambda: SimpleNamespace(static_version="release-42"),
    )
    assert pages._get_static_version() == "release-42"


def test_searches_crud(client, make_search):
    # create
    resp = client.post(
        "/api/searches",
        json={"title": "Python в Москве", "query": "python developer", "area_id": "1", "area_name": "Москва", "title_only": True},
    )
    assert resp.status_code == 201
    search_id = resp.json()["id"]
    assert resp.json()["title_only"] is True

    # duplicate
    dup = client.post(
        "/api/searches",
        json={"title": "Python в Москве", "query": "python", "area_id": "", "area_name": ""},
    )
    assert dup.status_code == 409

    # list
    rows = client.get("/api/searches").json()
    assert any(r["id"] == search_id for r in rows)

    # patch
    resp = client.patch(f"/api/searches/{search_id}", json={"active": False})
    assert resp.json()["active"] is False

    # put (edit)
    resp = client.put(
        f"/api/searches/{search_id}",
        json={"title": "Python в Москве 2", "query": "python", "area_id": "2", "area_name": "Питер", "title_only": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Python в Москве 2"
    assert body["query"] == "python"
    assert body["area_name"] == "Питер"
    assert body["active"] is False
    assert body["title_only"] is False

    # put duplicate title -> 409
    client.post("/api/searches", json={"title": "Другой", "query": "java", "area_id": "", "area_name": ""})
    resp = client.put(
        f"/api/searches/{search_id}",
        json={"title": "Другой", "query": "python", "area_id": "", "area_name": ""},
    )
    assert resp.status_code == 409

    # put unknown id -> 404
    resp = client.put(
        "/api/searches/999999",
        json={"title": "x", "query": "x", "area_id": "", "area_name": ""},
    )
    assert resp.status_code == 404

    # delete
    assert client.delete(f"/api/searches/{search_id}").status_code == 204
    assert client.delete(f"/api/searches/{search_id}").status_code == 404


def test_vacancies_filters(client, db, make_search):
    search = make_search()
    v1 = Vacancy(
        hh_id="111",
        title="Python Developer",
        url="https://hh.ru/vacancy/111",
        salary_from=100_000,
        salary_to=150_000,
        currency="RUR",
        employer="A",
        area="Москва",
        first_seen_at=utcnow(),
    )
    v2 = Vacancy(
        hh_id="222",
        title="Java Developer",
        url="https://hh.ru/vacancy/222",
        salary_from=None,
        salary_to=None,
        currency=None,
        employer="B",
        area="Питер",
        first_seen_at=utcnow(),
    )
    db.add_all([v1, v2])
    db.commit()

    resp = client.get("/api/vacancies")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    resp = client.get("/api/vacancies", params={"q": "java"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["hh_id"] == "222"

    resp = client.get("/api/vacancies", params={"salary_min": 120_000})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["hh_id"] == "111"

    # favorite toggle
    resp = client.post("/api/vacancies/111/favorite")
    assert resp.json()["is_favorite"] is True
    resp = client.get("/api/vacancies", params={"favorite": "true"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["hh_id"] == "111"

    assert client.post("/api/vacancies/999/favorite").status_code == 404


def test_vacancies_empty_search_id_not_422(client, db, make_search):
    make_search()
    db.add(Vacancy(hh_id="1", title="T", url="https://hh.ru/vacancy/1", first_seen_at=utcnow()))
    db.commit()

    resp = client.get("/api/vacancies", params={"search_id": ""})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = client.get("/vacancies", params={"search_id": "", "q": "python"})
    assert resp.status_code == 200


def test_vacancies_page_filters_by_employer(client, db):
    db.add_all(
        [
            Vacancy(
                hh_id="company-1",
                title="Python Developer",
                url="https://hh.ru/vacancy/company-1",
                employer="Компания А",
                first_seen_at=utcnow(),
            ),
            Vacancy(
                hh_id="company-2",
                title="Java Developer",
                url="https://hh.ru/vacancy/company-2",
                employer="Компания Б",
                first_seen_at=utcnow(),
            ),
            Vacancy(
                hh_id="company-3",
                title="Без работодателя",
                url="https://hh.ru/vacancy/company-3",
                employer=None,
                first_seen_at=utcnow(),
            ),
        ]
    )
    db.commit()

    resp = client.get("/vacancies")
    assert resp.status_code == 200
    assert '<select name="employer">' in resp.text
    assert '<option value="Компания А" >Компания А</option>' in resp.text
    assert '<option value="Компания Б" >Компания Б</option>' in resp.text

    resp = client.get("/vacancies", params={"employer": "Компания А"})
    assert resp.status_code == 200
    assert "Python Developer" in resp.text
    assert "Java Developer" not in resp.text
    assert '<option value="Компания А" selected>Компания А</option>' in resp.text

    soup = BeautifulSoup(resp.text, "html.parser")
    company_link = soup.select_one("a.company-filter-link")
    assert company_link is not None
    assert parse_qs(urlparse(company_link["href"]).query) == {
        "employer": ["Компания А"]
    }
    reset_link = soup.find("a", href="/vacancies", string="Сбросить фильтр")
    assert reset_link is not None


def test_vacancy_status(client, db):
    db.add(Vacancy(hh_id="77", title="T", url="https://hh.ru/vacancy/77", first_seen_at=utcnow()))
    db.commit()

    resp = client.post("/api/vacancies/77/status", json={"status": "Отклик"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "Отклик"
    assert resp.json()["applied_at"] is not None

    resp = client.post("/api/vacancies/77/status", json={"status": "Отказ"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "Отказ"

    db.expire_all()
    v = db.query(Vacancy).filter(Vacancy.hh_id == "77").one()
    assert v.applied_at is not None  # дата отклика сохраняется при переходе в Отказ

    resp = client.get("/api/vacancies", params={"status": "Отказ"})
    assert resp.json()["total"] == 1
    resp = client.get("/api/vacancies", params={"status": "не интересует"})
    assert resp.json()["total"] == 0

    resp = client.post("/api/vacancies/77/status", json={"status": ""})
    assert resp.json()["status"] is None
    resp = client.get("/api/vacancies", params={"status": "__none__"})
    assert resp.json()["total"] == 1

    assert client.post("/api/vacancies/999/status", json={"status": "Отказ"}).status_code == 404


def test_vacancy_rescore(client, db, make_search, monkeypatch):
    search = make_search(resume_url="https://example.com/resume.txt", ai_model="GigaChat-2-Pro")
    v = Vacancy(hh_id="55", title="T", url="https://hh.ru/vacancy/55", first_seen_at=utcnow())
    db.add(v)
    db.commit()

    from app.models import SearchVacancy

    db.add(SearchVacancy(search_id=search.id, vacancy_id=v.id))
    db.commit()

    async def fake_fetch_resume(self, url, max_chars=12000):
        return "Python, 5 лет, Москва"

    async def fake_score(self, spec, resume_text, vacancy):
        return 4

    from app import ai as ai_module

    monkeypatch.setattr(ai_module.AIClient, "fetch_resume_text", fake_fetch_resume)
    monkeypatch.setattr(ai_module.AIClient, "score_vacancy", fake_score)

    resp = client.post("/api/vacancies/55/rescore")
    assert resp.status_code == 200
    assert resp.json()["match_score"] == 4

    db.expire_all()
    v = db.query(Vacancy).filter(Vacancy.hh_id == "55").one()
    assert v.match_score == 4

    resp = client.get("/api/vacancies", params={"q": "T"})
    assert resp.json()["items"][0]["match_score"] == 4

    assert client.post("/api/vacancies/999/rescore").status_code == 404


def test_vacancy_rescore_no_search_config(client, db):
    db.add(Vacancy(hh_id="66", title="T", url="https://hh.ru/vacancy/66", first_seen_at=utcnow()))
    db.commit()
    resp = client.post("/api/vacancies/66/rescore")
    assert resp.status_code == 400


def test_stats(client, db):
    db.add(Vacancy(hh_id="1", title="T", url="https://hh.ru/vacancy/1",
                   first_seen_at=utcnow()))
    db.commit()
    data = client.get("/api/stats?days=7").json()
    assert data["total"] == 1
    assert len(data["days"]) == 7
    assert data["days"][-1]["count"] == 1


def test_stats_date_from_excludes_initial_import_but_keeps_total(client, db):
    db.add_all(
        [
            Vacancy(
                hh_id="initial-import",
                title="Первичная загрузка",
                url="https://hh.ru/vacancy/initial-import",
                first_seen_at=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
            ),
            Vacancy(
                hh_id="regular-vacancy",
                title="Обычная вакансия",
                url="https://hh.ru/vacancy/regular-vacancy",
                first_seen_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            ),
        ]
    )
    db.commit()

    data = client.get("/api/stats", params={"date_from": "2026-08-18"}).json()
    days = {row["date"]: row for row in data["days"]}

    assert "2026-08-17" not in days
    assert days["2026-08-18"]["count"] == 1
    assert data["total"] == 2


def test_dashboard_uses_configured_stats_start_date(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert 'data-stats-from="2026-08-18"' in resp.text
    assert "Новые вакансии и отклики с 18.08.2026" in resp.text


def test_areas_suggest(client, monkeypatch):
    def fake_fetch_tree(_settings):
        return [
            {"id": "113", "name": "Россия", "areas": [
                {"id": "1", "name": "Москва", "areas": []},
                {"id": "2", "name": "Санкт-Петербург", "areas": []},
            ]}
        ]

    import app.areas as areas_service

    monkeypatch.setattr(areas_service, "_fetch_tree", fake_fetch_tree)
    items = client.get("/api/areas", params={"q": "москв"}).json()
    assert items == [{"id": "1", "name": "Россия, Москва"}]
