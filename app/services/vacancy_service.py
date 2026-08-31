from datetime import datetime

from sqlalchemy.orm import Session

from ..hh_scraper import VacancyCard
from ..models import Search, SearchVacancy, Vacancy, utcnow


def ingest_vacancies(db: Session, search: Search, cards: list[VacancyCard]) -> list[Vacancy]:
    """Сохраняет найденные вакансии, связывает с поиском, возвращает только новые."""
    new_vacancies: list[Vacancy] = []
    history = _build_vacancy_history(db)
    for card in cards:
        vacancy = db.query(Vacancy).filter(Vacancy.hh_id == card.hh_id).first()
        if vacancy is None:
            identity = _vacancy_identity(card.title, card.employer)
            previous = history.get(identity) if identity else None
            vacancy = Vacancy(
                hh_id=card.hh_id,
                title=card.title,
                url=card.url,
                salary_from=card.salary_from,
                salary_to=card.salary_to,
                currency=card.currency,
                gross=card.gross,
                employer=card.employer,
                area=card.area,
                experience=card.experience,
                first_seen_at=utcnow(),
                status=previous.status if previous else None,
            )
            if previous:
                vacancy._is_repeat = True
                vacancy._previous_status = previous.status
                vacancy._previous_hh_id = previous.hh_id
            db.add(vacancy)
            db.flush()
            new_vacancies.append(vacancy)
            if identity:
                current = history.get(identity)
                if current is None or (vacancy.status and not current.status):
                    history[identity] = vacancy
        else:
            _refresh_fields(vacancy, card)

        linked = (
            db.query(SearchVacancy)
            .filter_by(search_id=search.id, vacancy_id=vacancy.id)
            .first()
        )
        if linked is None:
            db.add(SearchVacancy(search_id=search.id, vacancy_id=vacancy.id))

    db.commit()
    return new_vacancies


def _normalize_identity_part(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _vacancy_identity(title: str | None, employer: str | None) -> tuple[str, str] | None:
    normalized_title = _normalize_identity_part(title)
    normalized_employer = _normalize_identity_part(employer)
    if not normalized_title or not normalized_employer:
        return None
    return normalized_title, normalized_employer


def _build_vacancy_history(db: Session) -> dict[tuple[str, str], Vacancy]:
    """Возвращает по каждой паре название/компания свежую запись с заполненным статусом.

    Если заполненного статуса нет, сохраняется самая свежая запись — она всё равно
    нужна, чтобы пометить новую вакансию как повтор.
    """
    history: dict[tuple[str, str], Vacancy] = {}
    vacancies = (
        db.query(Vacancy)
        .order_by(Vacancy.first_seen_at.desc(), Vacancy.id.desc())
        .all()
    )
    for vacancy in vacancies:
        identity = _vacancy_identity(vacancy.title, vacancy.employer)
        if identity is None:
            continue
        current = history.get(identity)
        if current is None or (vacancy.status and not current.status):
            history[identity] = vacancy
    return history


def _refresh_fields(vacancy: Vacancy, card: VacancyCard) -> None:
    """Обновляет изменяемые поля известной вакансии."""
    if card.title and card.title != vacancy.title:
        vacancy.title = card.title
    if card.employer and card.employer != vacancy.employer:
        vacancy.employer = card.employer
    if card.salary_from is not None or card.salary_to is not None:
        vacancy.salary_from = card.salary_from
        vacancy.salary_to = card.salary_to
        vacancy.currency = card.currency
        vacancy.gross = card.gross
    if card.area:
        vacancy.area = card.area


def salary_label(vacancy: Vacancy) -> str:
    if vacancy.salary_from is None and vacancy.salary_to is None:
        return "з/п не указана"
    if vacancy.salary_from is not None and vacancy.salary_to is not None:
        if vacancy.salary_from == vacancy.salary_to:
            return _fmt(vacancy.salary_from, vacancy.currency)
        return f"{_fmt(vacancy.salary_from, None)} – {_fmt(vacancy.salary_to, vacancy.currency)}"
    if vacancy.salary_from is not None:
        return f"от {_fmt(vacancy.salary_from, vacancy.currency)}"
    return f"до {_fmt(vacancy.salary_to, vacancy.currency)}"


def _fmt(value: int, currency: str | None) -> str:
    text = f"{value:,}".replace(",", " ")
    return f"{text} {currency or '₽'}"
