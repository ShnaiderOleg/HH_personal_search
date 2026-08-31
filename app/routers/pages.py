from datetime import datetime, timedelta, timezone
from hashlib import sha256

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


def _get_static_version() -> str:
    configured_version = get_settings().static_version.strip()
    if configured_version:
        return configured_version

    digest = sha256()
    for filename in ("style.css", "app.js"):
        digest.update((BASE_DIR / "static" / filename).read_bytes())
    return digest.hexdigest()[:12]


templates.env.globals["static_version"] = _get_static_version()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_session)):
    recent = (
        db.query(Vacancy)
        .filter(Vacancy.status.is_(None))
        .order_by(Vacancy.first_seen_at.desc())
        .limit(10)
        .all()
    )
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
    employer: str | None = None,
    favorite: bool | None = None,
    status: str | None = None,
    db: Session = Depends(get_session),
):
    employers = [
        row[0]
        for row in (
            db.query(Vacancy.employer)
            .filter(Vacancy.employer.isnot(None), func.trim(Vacancy.employer) != "")
            .distinct()
            .order_by(Vacancy.employer.asc())
            .all()
        )
    ]
    query = db.query(Vacancy)
    if favorite:
        query = query.filter(Vacancy.is_favorite.is_(True))
    if status == EMPTY_STATUS:
        query = query.filter(Vacancy.status.is_(None))
    elif status:
        query = query.filter(Vacancy.status == status)
    if q:
        query = query.filter(Vacancy.title.ilike(f"%{q}%"))
    if employer:
        query = query.filter(Vacancy.employer == employer)
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
            "employers": employers,
            "salary_label": salary_label,
            "current_search_id": sid,
            "current_q": q or "",
            "current_employer": employer or "",
            "current_favorite": favorite,
            "current_status": status or "",
            "EMPTY_STATUS": EMPTY_STATUS,
        },
    )
