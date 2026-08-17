from datetime import datetime

from sqlalchemy.orm import Session

from ..hh_scraper import VacancyCard
from ..models import Search, SearchVacancy, Vacancy, utcnow


def ingest_vacancies(db: Session, search: Search, cards: list[VacancyCard]) -> list[Vacancy]:
    """Сохраняет найденные вакансии, связывает с поиском, возвращает только новые."""
    new_vacancies: list[Vacancy] = []
    for card in cards:
        vacancy = db.query(Vacancy).filter(Vacancy.hh_id == card.hh_id).first()
        if vacancy is None:
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
            )
            db.add(vacancy)
            db.flush()
            new_vacancies.append(vacancy)
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
