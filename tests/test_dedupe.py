from app.hh_scraper import VacancyCard
from app.services.vacancy_service import ingest_vacancies


def _card(hh_id, title="Вакансия"):
    return VacancyCard(
        hh_id=hh_id,
        title=title,
        url=f"https://hh.ru/vacancy/{hh_id}",
        salary_from=100_000,
        salary_to=None,
        currency="RUR",
        employer="Компания",
        area="Москва",
        experience=None,
        activity_text="Сегодня",
    )


def test_new_vacancy_created_and_linked(db, make_search):
    search = make_search()
    cards = [_card("1"), _card("2")]
    new = ingest_vacancies(db, search, cards)

    assert [v.hh_id for v in new] == ["1", "2"]
    assert search.vacancies is not None and len(search.vacancies) == 2


def test_second_run_no_duplicates(db, make_search):
    search = make_search()
    ingest_vacancies(db, search, [_card("1"), _card("2")])

    again = ingest_vacancies(db, search, [_card("1"), _card("2"), _card("3")])
    assert [v.hh_id for v in again] == ["3"]
    assert len(search.vacancies) == 3


def test_shared_between_searches_notify_once(db, make_search):
    s1 = make_search(title="Поиск 1", query="python")
    s2 = make_search(title="Поиск 2", query="backend")

    first = ingest_vacancies(db, s1, [_card("1")])
    second = ingest_vacancies(db, s2, [_card("1")])

    assert len(first) == 1
    assert second == []
    assert len(s1.vacancies) == 1
    assert len(s2.vacancies) == 1


def test_fields_refresh(db, make_search):
    search = make_search()
    ingest_vacancies(db, search, [_card("1")])

    updated = VacancyCard(
        hh_id="1", title="Новое название", url="https://hh.ru/vacancy/1",
        salary_from=150_000, salary_to=200_000, currency="RUR",
        employer="Другая компания", area="Москва", experience=None, activity_text="Сегодня",
    )
    ingest_vacancies(db, search, [updated])

    from app.models import Vacancy

    stored = db.query(Vacancy).filter(Vacancy.hh_id == "1").one()
    assert stored.title == "Новое название"
    assert stored.salary_from == 150_000
    assert stored.salary_to == 200_000
    assert stored.employer == "Другая компания"
