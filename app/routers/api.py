import asyncio
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..models import EMPTY_STATUS, Search, SearchVacancy, Vacancy, utcnow
from ..notifications import telegram as telegram_notifier
from ..services.vacancy_service import salary_label
from ..state import get_poller
from .. import areas as areas_service

router = APIRouter()


class SearchIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=200)
    area_id: str = ""
    area_name: str = ""
    title_only: bool = False


class SearchPatch(BaseModel):
    active: bool | None = None


class VacancyStatus(BaseModel):
    status: str | None = Field(default=None, max_length=30)


def _search_out(search: Search) -> dict:
    return {
        "id": search.id,
        "title": search.title,
        "query": search.query,
        "area_id": search.area_id,
        "area_name": search.area_name,
        "title_only": search.title_only,
        "active": search.active,
        "created_at": search.created_at,
        "last_run_at": search.last_run_at,
        "last_error": search.last_error,
    }


def _vacancy_out(vacancy: Vacancy, search_ids: list[int] | None = None) -> dict:
    return {
        "id": vacancy.id,
        "hh_id": vacancy.hh_id,
        "title": vacancy.title,
        "url": vacancy.url,
        "salary_label": salary_label(vacancy),
        "salary_from": vacancy.salary_from,
        "salary_to": vacancy.salary_to,
        "currency": vacancy.currency,
        "employer": vacancy.employer,
        "area": vacancy.area,
        "experience": vacancy.experience,
        "first_seen_at": vacancy.first_seen_at,
        "is_favorite": vacancy.is_favorite,
        "status": vacancy.status,
        "applied_at": vacancy.applied_at,
        "search_ids": search_ids or [],
    }


# --- Поиски ---

@router.get("/searches")
def list_searches(db: Session = Depends(get_session)):
    rows = db.query(Search).order_by(Search.created_at.desc()).all()
    return [_search_out(s) for s in rows]


@router.post("/searches", status_code=201)
def create_search(payload: SearchIn, db: Session = Depends(get_session)):
    if db.query(Search).filter(Search.title == payload.title).first():
        raise HTTPException(409, "Поиск с таким названием уже есть")
    search = Search(**payload.model_dump())
    db.add(search)
    db.commit()
    db.refresh(search)
    return _search_out(search)


@router.put("/searches/{search_id}")
def update_search(search_id: int, payload: SearchIn, db: Session = Depends(get_session)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Поиск не найден")
    if (
        db.query(Search)
        .filter(Search.title == payload.title, Search.id != search_id)
        .first()
    ):
        raise HTTPException(409, "Поиск с таким названием уже есть")
    search.title = payload.title
    search.query = payload.query
    search.area_id = payload.area_id
    search.area_name = payload.area_name
    search.title_only = payload.title_only
    db.commit()
    db.refresh(search)
    return _search_out(search)


@router.patch("/searches/{search_id}")
def patch_search(search_id: int, payload: SearchPatch, db: Session = Depends(get_session)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Поиск не найден")
    if payload.active is not None:
        search.active = payload.active
    db.commit()
    db.refresh(search)
    return _search_out(search)


@router.delete("/searches/{search_id}", status_code=204)
def delete_search(search_id: int, db: Session = Depends(get_session)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Поиск не найден")
    db.delete(search)
    db.commit()


# --- Вакансии ---

@router.get("/vacancies")
def list_vacancies(
    search_id: str | None = None,
    q: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    favorite: bool | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
):
    query = db.query(Vacancy)
    if favorite:
        query = query.filter(Vacancy.is_favorite.is_(True))
    if status == EMPTY_STATUS:
        query = query.filter(Vacancy.status.is_(None))
    elif status:
        query = query.filter(Vacancy.status == status)
    if q:
        query = query.filter(Vacancy.title.ilike(f"%{q}%"))
    if salary_min is not None:
        query = query.filter(
            (Vacancy.salary_from >= salary_min) | (Vacancy.salary_to >= salary_min)
        )
    if salary_max is not None:
        query = query.filter(
            (Vacancy.salary_from <= salary_max) | (Vacancy.salary_to <= salary_max)
        )
    sid = int(search_id) if search_id else None
    if sid is not None:
        query = query.join(SearchVacancy).filter(SearchVacancy.search_id == sid)

    total = query.count()
    rows = (
        query.order_by(Vacancy.first_seen_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_vacancy_out(v) for v in rows],
    }


@router.post("/vacancies/{hh_id}/favorite")
def toggle_favorite(hh_id: str, db: Session = Depends(get_session)):
    vacancy = db.query(Vacancy).filter(Vacancy.hh_id == hh_id).first()
    if vacancy is None:
        raise HTTPException(404, "Вакансия не найдена")
    vacancy.is_favorite = not vacancy.is_favorite
    db.commit()
    db.refresh(vacancy)
    return {"hh_id": hh_id, "is_favorite": vacancy.is_favorite}


@router.post("/vacancies/{hh_id}/status")
def set_vacancy_status(hh_id: str, payload: VacancyStatus, db: Session = Depends(get_session)):
    vacancy = db.query(Vacancy).filter(Vacancy.hh_id == hh_id).first()
    if vacancy is None:
        raise HTTPException(404, "Вакансия не найдена")
    vacancy.status = payload.status or None
    if vacancy.status == "Отклик":
        vacancy.applied_at = utcnow()
    db.commit()
    db.refresh(vacancy)
    return {
        "hh_id": hh_id,
        "status": vacancy.status,
        "applied_at": vacancy.applied_at,
    }


# --- Регионы ---

@router.get("/areas")
def areas(q: str = Query("", max_length=100)):
    settings = get_settings()
    return areas_service.search_areas(settings, q)


# --- Статистика ---

@router.get("/stats")
def stats(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_session)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(func.date(Vacancy.first_seen_at), func.count(Vacancy.id))
        .filter(Vacancy.first_seen_at >= since)
        .group_by(func.date(Vacancy.first_seen_at))
        .all()
    )
    counts = {str(day): cnt for day, cnt in rows}
    result = []
    for i in range(days - 1, -1, -1):
        day = (date.today() - timedelta(days=i)).isoformat()
        result.append({"date": day, "count": counts.get(day, 0)})
    return {
        "days": result,
        "total": db.query(Vacancy).count(),
        "favorites": db.query(Vacancy).filter(Vacancy.is_favorite.is_(True)).count(),
        "searches": db.query(Search).filter(Search.active.is_(True)).count(),
    }


# --- Ручной запуск / тест ---

@router.post("/poll/run")
async def run_poll():
    poller = get_poller()
    if poller is None:
        raise HTTPException(503, "поллер не запущен")
    asyncio.get_running_loop().create_task(poller.run_once())
    return {"status": "started"}


@router.post("/notify/test")
async def notify_test():
    settings = get_settings()
    sent = await telegram_notifier.send_test_telegram(settings)
    return {"sent": sent, "configured": bool(settings.tg_bot_token and settings.tg_chat_id_list)}
