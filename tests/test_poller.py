import pytest

from app.anti_bot import ScraperBlockedError
from app.config import get_settings
from app.hh_scraper import HHScraper, VacancyCard
from app.poller import Poller


def _cards(n):
    return [
        VacancyCard(hh_id=str(i), title=f"v{i}", url=f"https://hh.ru/vacancy/{i}")
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_blocked_on_first_page_still_marks_run(db, make_search):
    async def boom(*args, **kwargs):
        raise ScraperBlockedError("captcha/anti-bot page detected")

    scraper = object.__new__(HHScraper)
    scraper.fetch_serp = boom

    poller = Poller(get_settings())
    notified = []

    async def fake_notify(vacancies):
        notified.append(vacancies)

    poller._notify = fake_notify

    search = make_search()
    await poller._process_search(scraper, db, search)

    db.refresh(search)
    assert search.last_run_at is not None
    assert "блокировка" in (search.last_error or "")
    assert notified == []


@pytest.mark.asyncio
async def test_score_before_notify(db, make_search):
    """Оценка нейросетью выставляется ДО отправки уведомления в Telegram."""

    class FakeScraper:
        async def fetch_serp(self, query, area_id="", page=0,
                             order_by="publication_time", title_only=False):
            return "<html>"

        def parse_serp(self, html):
            return _cards(3)

    poller = Poller(get_settings())
    scored = []
    notified = []

    async def fake_score(db, search, vacancies):
        for v in vacancies:
            v.match_score = 4
        scored.append(len(vacancies))

    async def fake_notify(vacancies):
        notified.append([(v.hh_id, v.match_score) for v in vacancies])

    poller._score_new_vacancies = fake_score
    poller._notify = fake_notify

    search = make_search(resume_url="https://disk.yandex.ru/i/xxx", ai_model="GigaChat-2-Max")
    await poller._process_search(FakeScraper(), db, search)

    assert scored == [3]
    assert notified == [[("0", 4), ("1", 4), ("2", 4)]]


@pytest.mark.asyncio
async def test_blocked_after_page0_still_ingests_and_notifies(db, make_search):
    async def fake_fetch(query, area_id="", page=0, order_by="publication_time", title_only=False):
        if page >= 1:
            raise ScraperBlockedError("captcha")
        return "<html>page</html>"

    class FakeScraper:
        async def fetch_serp(self, query, area_id="", page=0,
                             order_by="publication_time", title_only=False):
            return await fake_fetch(query, area_id, page, order_by, title_only)

        def parse_serp(self, html):
            return _cards(20)

    poller = Poller(get_settings())
    notified = []

    async def fake_notify(vacancies):
        notified.append(vacancies)

    poller._notify = fake_notify

    search = make_search()
    await poller._process_search(FakeScraper(), db, search)

    db.refresh(search)
    assert search.last_run_at is not None
    assert "блокировка" in (search.last_error or "")
    assert len(notified) == 1
    assert len(notified[0]) == 20
