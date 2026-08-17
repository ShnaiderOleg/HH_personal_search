from datetime import date, timedelta

from app.hh_scraper import HHScraper, parse_activity_date, parse_salary

from app.config import Settings

SAMPLE_SERP = """
<html><body>
<article data-qa="vacancy-serp__vacancy">
  <a class="magritte-link" data-qa="serp-item__title" href="https://hh.ru/vacancy/136190065">
    <span data-qa="serp-item__title-text">Senior Python Backend Developer</span>
  </a>
  <div><span class="magritte-text__salary"><data value="300000">300 000</data> <data value="RUB">&#8381;</data> за месяц, на руки</span></div>
  <a data-qa="vacancy-serp__vacancy-employer" href="https://hh.ru/employer/1455">ООО Пример</a>
  <span data-qa="vacancy-serp__vacancy-address">Москва</span>
  <span data-qa="vacancy-serp__vacancy-work-experience-between3And6">Опыт 3–6 лет</span>
  <span data-qa="vacancy-serp-item-activity">Обновлено вчера</span>
</article>
<article data-qa="vacancy-serp__vacancy">
  <a class="magritte-link" data-qa="serp-item__title" href="https://hh.ru/vacancy/999">
    <span data-qa="serp-item__title-text">Junior QA</span>
  </a>
  <div><span class="magritte-text__salary"><data value="80000">80 000</data> <data value="RUB">&#8381;</data> за месяц</span></div>
  <a data-qa="vacancy-serp__vacancy-employer" href="#">Агентство</a>
  <span data-qa="vacancy-serp__vacancy-address">Санкт-Петербург</span>
</article>
</body></html>
"""


def make_settings():
    return Settings(hh_base_url="https://hh.ru", _env_file=None)


def test_parse_serp():
    cards = HHScraper(make_settings()).parse_serp(SAMPLE_SERP)
    assert len(cards) == 2

    first = cards[0]
    assert first.hh_id == "136190065"
    assert first.title == "Senior Python Backend Developer"
    assert first.salary_from == 300_000
    assert first.salary_to is None
    assert first.currency == "RUR"
    assert first.gross is False
    assert first.employer == "ООО Пример"
    assert first.area == "Москва"
    assert first.experience == "Опыт 3–6 лет"
    assert "вчера" in first.activity_text.lower()

    second = cards[1]
    assert second.hh_id == "999"
    assert second.salary_from == 80_000
    assert second.salary_to is None
    assert second.currency == "RUR"


def test_parse_salary():
    cases = [
        ("от 250 000 ₽", (250_000, None, "RUR", None)),
        ("до 300 000 руб.", (None, 300_000, "RUR", None)),
        ("250 000 – 300 000 ₽", (250_000, 300_000, "RUR", None)),
        ("от 150 000 до 200 000 ₽", (150_000, 200_000, "RUR", None)),
        ("100000", (100_000, None, "RUR", None)),
        ("з/п не указана", (None, None, "RUR", None)),
        ("от 5 000 $", (5_000, None, "USD", None)),
        ("до 200 000 ₽ до вычета налогов", (None, 200_000, "RUR", True)),
        ("150 000 ₽ на руки", (150_000, None, "RUR", False)),
    ]
    for text, expected in cases:
        assert parse_salary(text) == expected, text


def test_parse_activity_date():
    today = date(2026, 8, 17)
    assert parse_activity_date("Сегодня", today) == today
    assert parse_activity_date("Обновлено вчера", today) == today - timedelta(days=1)
    assert parse_activity_date("Обновлено 14 дней назад", today) == today - timedelta(days=14)
    assert parse_activity_date("3 дня назад", today) == today - timedelta(days=3)
    assert parse_activity_date(None, today) is None
    assert parse_activity_date("з/п", today) is None
