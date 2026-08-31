import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..ai import AIClient, get_ai_model
from ..models import EMPTY_STATUS, Search, SearchVacancy, Vacancy, utcnow
from ..notifications import telegram as telegram_notifier
from ..services.vacancy_service import salary_label
from ..state import get_poller
from ..timeutil import ensure_utc
from .. import areas as areas_service

router = APIRouter()
logger = logging.getLogger(__name__)


class SearchIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=200)
    area_id: str = ""
    area_name: str = ""
    title_only: bool = False
    resume_url: str = ""
    ai_model: str = ""


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
        "resume_url": search.resume_url,
        "ai_model": search.ai_model,
        "active": search.active,
        "created_at": ensure_utc(search.created_at),
        "last_run_at": ensure_utc(search.last_run_at),
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
        "first_seen_at": ensure_utc(vacancy.first_seen_at),
        "is_favorite": vacancy.is_favorite,
        "status": vacancy.status,
        "applied_at": ensure_utc(vacancy.applied_at),
        "match_score": vacancy.match_score,
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
    data = payload.model_dump()
    data["resume_url"] = (data.get("resume_url") or "").strip() or None
    data["ai_model"] = (data.get("ai_model") or "").strip() or None
    search = Search(**data)
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
    search.resume_url = payload.resume_url.strip() or None
    search.ai_model = payload.ai_model.strip() or None
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


def _pick_score_search(db, vacancy_id: int, search_id: int | None) -> Search | None:
    """Возвращает поиск с настроенным резюме/нейросетью для оценки вакансии."""
    if search_id is not None:
        search = db.get(Search, search_id)
        if search and search.resume_url and search.ai_model:
            return search
    rows = (
        db.query(Search)
        .join(SearchVacancy)
        .filter(SearchVacancy.vacancy_id == vacancy_id)
        .order_by(Search.created_at.desc())
        .all()
    )
    return next((s for s in rows if s.resume_url and s.ai_model), None)


@router.post("/vacancies/{hh_id}/rescore")
async def rescore_vacancy(
    hh_id: str,
    search_id: int | None = None,
    db: Session = Depends(get_session),
):
    """Принудительно пересчитывает оценку соответствия вакансии нейросетью."""
    vacancy = db.query(Vacancy).filter(Vacancy.hh_id == hh_id).first()
    if vacancy is None:
        raise HTTPException(404, "Вакансия не найдена")
    settings = get_settings()
    search = _pick_score_search(db, vacancy.id, search_id)
    if search is None:
        raise HTTPException(
            400, "У вакансии нет поиска с настроенным резюме и нейросетью"
        )
    spec = get_ai_model(search.ai_model)
    if spec is None:
        raise HTTPException(400, f"Нейросеть '{search.ai_model}' не найдена в списке")
    if spec["provider"] == "gigachat" and not settings.gigachat_auth_key:
        raise HTTPException(400, "GigaChat не настроен (GIGACHAT_AUTH_KEY пуст)")
    if spec["provider"] == "proxyapi" and not settings.proxyapi_api_key:
        raise HTTPException(400, "ProxyAPI не настроен (PROXYAPI_API_KEY пуст)")
    client = AIClient(settings)
    try:
        resume_text = await client.fetch_resume_text(search.resume_url)
        if not resume_text.strip():
            raise HTTPException(400, "Резюме по ссылке пустое или недоступно")
        score = await client.score_vacancy(spec, resume_text, vacancy)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("rescore вакансии %s не удалось", hh_id)
        raise HTTPException(502, f"Ошибка нейросети: {exc}") from exc
    if score is None:
        raise HTTPException(502, "Нейросеть не вернула оценку")
    vacancy.match_score = score
    db.commit()
    db.refresh(vacancy)
    return {"hh_id": hh_id, "match_score": score}


# --- Регионы ---

@router.get("/areas")
def areas(q: str = Query("", max_length=100)):
    settings = get_settings()
    return areas_service.search_areas(settings, q)


# --- Статистика ---

@router.get("/stats")
def stats(
    days: int = Query(30, ge=1, le=365),
    date_from: date | None = Query(None),
    db: Session = Depends(get_session),
):
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone)
    today = datetime.now(tz).date()
    start_date = date_from or (today - timedelta(days=days - 1))
    start_date_iso = start_date.isoformat()
    rows = (
        db.query(func.date(Vacancy.first_seen_at), func.count(Vacancy.id))
        .filter(func.date(Vacancy.first_seen_at) >= start_date_iso)
        .group_by(func.date(Vacancy.first_seen_at))
        .all()
    )
    counts = {str(day): cnt for day, cnt in rows}
    applied_rows = (
        db.query(func.date(Vacancy.applied_at), func.count(Vacancy.id))
        .filter(
            Vacancy.applied_at.is_not(None),
            func.date(Vacancy.applied_at) >= start_date_iso,
        )
        .group_by(func.date(Vacancy.applied_at))
        .all()
    )
    applied_counts = {str(day): cnt for day, cnt in applied_rows}
    result = []
    day_count = max(0, (today - start_date).days + 1)
    for offset in range(day_count):
        day = (start_date + timedelta(days=offset)).isoformat()
        result.append({
            "date": day,
            "count": counts.get(day, 0),
            "applied": applied_counts.get(day, 0),
        })
    return {
        "days": result,
        "total": db.query(Vacancy).count(),
        "favorites": db.query(Vacancy).filter(Vacancy.is_favorite.is_(True)).count(),
        "applied": db.query(Vacancy).filter(Vacancy.status == "Отклик").count(),
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
