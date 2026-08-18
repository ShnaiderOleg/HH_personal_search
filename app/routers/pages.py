from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..models import EMPTY_STATUS, Search, Vacancy
from ..ai import get_ai_model, load_ai_models
from ..services.vacancy_service import salary_label
from ..timeutil import fmt_dt

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["fmt_dt"] = lambda dt: fmt_dt(dt, get_settings().app_timezone)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_session)):
    recent = db.query(Vacancy).order_by(Vacancy.first_seen_at.desc()).limit(10).all()
    searches = db.query(Search).order_by(Search.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "recent": recent,
            "searches": searches,
            "salary_label": salary_label,
            "now": datetime.now(timezone.utc),
        },
    )


@router.get("/searches", response_class=HTMLResponse)
def searches_page(request: Request, db: Session = Depends(get_session)):
    searches = db.query(Search).order_by(Search.created_at.desc()).all()
    ai_model_labels = {m.get("model"): m.get("label", m.get("model")) for m in load_ai_models()}
    return templates.TemplateResponse(
        request,
        "searches.html",
        {
            "active_page": "searches",
            "searches": searches,
            "ai_models": load_ai_models(),
            "ai_model_labels": ai_model_labels,
        },
    )


@router.get("/vacancies", response_class=HTMLResponse)
def vacancies_page(
    request: Request,
    search_id: str | None = None,
    q: str | None = None,
    favorite: bool | None = None,
    status: str | None = None,
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
    sid = int(search_id) if search_id else None
    if sid is not None:
        from ..models import SearchVacancy

        query = query.join(SearchVacancy).filter(SearchVacancy.search_id == sid)
    vacancies = query.order_by(Vacancy.first_seen_at.desc()).limit(100).all()
    searches = db.query(Search).order_by(Search.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "vacancies.html",
        {
            "active_page": "vacancies",
            "vacancies": vacancies,
            "searches": searches,
            "salary_label": salary_label,
            "current_search_id": sid,
            "current_q": q or "",
            "current_favorite": favorite,
            "current_status": status or "",
            "EMPTY_STATUS": EMPTY_STATUS,
        },
    )
