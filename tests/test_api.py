from datetime import datetime, timezone

import pytest

from app.models import Vacancy, utcnow


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


def test_stats(client, db):
    db.add(Vacancy(hh_id="1", title="T", url="https://hh.ru/vacancy/1",
                   first_seen_at=utcnow()))
    db.commit()
    data = client.get("/api/stats?days=7").json()
    assert data["total"] == 1
    assert len(data["days"]) == 7
    assert data["days"][-1]["count"] == 1


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
