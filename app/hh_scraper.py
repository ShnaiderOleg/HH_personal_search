import re
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from .anti_bot import ScraperBlockedError
from .config import Settings

_SALARY_NUM_RE = re.compile(r"\d[\d\s]{2,}\d")
_CURRENCIES = [
    ("\u20b8", "KZT"), ("\u20ac", "EUR"), ("$", "USD"), ("\u20bd", "RUR"),
    ("руб", "RUR"), ("тенге", "KZT"), ("долл", "USD"), ("евро", "EUR"),
]
_CURRENCY_CODES = {"RUB": "RUR", "RUR": "RUR", "USD": "USD", "EUR": "EUR", "KZT": "KZT"}


class VacancyCard:
    """Мини-представление вакансии из выдачи (SERP)."""

    __slots__ = (
        "hh_id", "title", "url", "salary_from", "salary_to", "currency",
        "gross", "employer", "area", "experience", "activity_text",
    )

    def __init__(self, hh_id, title, url, salary_from=None, salary_to=None,
                 currency=None, gross=None, employer=None, area=None,
                 experience=None, activity_text=None):
        self.hh_id = hh_id
        self.title = title
        self.url = url
        self.salary_from = salary_from
        self.salary_to = salary_to
        self.currency = currency
        self.gross = gross
        self.employer = employer
        self.area = area
        self.experience = experience
        self.activity_text = activity_text

    def __repr__(self):  # pragma: no cover
        return f"<VacancyCard {self.hh_id} {self.title!r}>"


def parse_salary(text: str | None, fallback_numbers=None, fallback_currency=None):
    """Разбирает строку зп вида 'от 250 000 ₽', 'до 300 000 руб.', '250 000 – 300 000 ₽'.

    fallback_* используются, если текст пустой, но числа извлечены из <data> элементов.
    Возвращает (salary_from, salary_to, currency, gross).
    """
    currency = fallback_currency or "RUR"
    gross = None
    numbers = list(fallback_numbers or [])
    raw = ""
    low = ""

    if text:
        raw = text.replace("\xa0", " ").replace("\u202f", " ").strip()
        low = raw.lower()
        for symbol, code in _CURRENCIES:
            if symbol in low:
                currency = code
                break
        gross = True if "до вычета" in low else (False if "на руки" in low else None)
        numbers = [int(n.replace(" ", "")) for n in _SALARY_NUM_RE.findall(raw)] or numbers

    if not numbers:
        return None, None, currency, None

    if "от" in low and "до" in low and len(numbers) >= 2:
        return numbers[0], numbers[1], currency, gross
    if "–" in raw or "—" in raw or "-" in raw:
        if len(numbers) >= 2:
            return numbers[0], numbers[1], currency, gross
        return numbers[0], None, currency, gross
    if "от" in low:
        return numbers[0], None, currency, gross
    if "до" in low:
        return None, numbers[0], currency, gross
    if len(numbers) >= 2 and not text:
        return numbers[0], numbers[1], currency, gross
    return numbers[0], None, currency, gross


def parse_activity_date(text: str | None, today: date | None = None) -> date | None:
    """Переводит 'Сегодня'/'Вчера'/'N дней назад'/'Обновлено ...' в дату."""
    if not text:
        return None
    today = today or date.today()
    low = text.lower()
    if "сегодня" in low:
        return today
    if "вчера" in low:
        return today - timedelta(days=1)
    m = re.search(r"(\d+)\s+дн", low)
    if m:
        return today - timedelta(days=int(m.group(1)))
    return None


class HHScraper:
    """Анонимный парсер выдачи hh.ru (опция входа — через cookies)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        headers = {
            "User-Agent": settings.hh_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": f"{settings.hh_base_url}/",
        }
        if settings.cookie_header:
            headers["Cookie"] = settings.cookie_header
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=settings.scrape_timeout_seconds,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def fetch_serp(self, query: str, area_id: str = "", page: int = 0,
                         order_by: str = "publication_time",
                         title_only: bool = False) -> str:
        params = {"text": query, "page": page}
        if area_id:
            params["area"] = area_id
        if order_by:
            params["order_by"] = order_by
        if title_only:
            params["search_field"] = "name"
        try:
            resp = await self.client.get(
                f"{self.settings.hh_base_url}/search/vacancy", params=params
            )
        except httpx.HTTPError as exc:
            raise ScraperBlockedError(f"network error: {exc}") from exc

        if resp.status_code in (403, 429, 503):
            raise ScraperBlockedError(f"blocked by hh.ru: HTTP {resp.status_code}")
        resp.raise_for_status()
        if self._looks_blocked(resp.text):
            raise ScraperBlockedError("captcha/anti-bot page detected")
        return resp.text

    @staticmethod
    def _looks_blocked(html: str) -> bool:
        low = html.lower()
        return "captcha" in low and "serp-item" not in low

    def parse_serp(self, html: str) -> list[VacancyCard]:
        soup = BeautifulSoup(html, "lxml")
        cards: list[VacancyCard] = []
        for article in soup.select('article[data-qa="vacancy-serp__vacancy"]'):
            link = article.select_one('a[data-qa="serp-item__title"]')
            if link is None:
                continue
            href = link.get("href", "")
            match = re.search(r"/vacancy/(\d+)", href)
            if not match:
                continue
            hh_id = match.group(1)

            salary_from, salary_to, currency, gross = self._extract_salary(article)

            employer_node = article.select_one(
                '[data-qa="vacancy-serp__vacancy-employer"]'
            )
            area_node = article.select_one(
                '[data-qa="vacancy-serp__vacancy-address"]'
            )
            exp_node = article.select_one(
                '[data-qa^="vacancy-serp__vacancy-work-experience"]'
            )
            activity_node = article.select_one(
                '[data-qa="vacancy-serp-item-activity"]'
            )

            cards.append(VacancyCard(
                hh_id=hh_id,
                title=link.get_text(" ", strip=True),
                url=f"{self.settings.hh_base_url}/vacancy/{hh_id}",
                salary_from=salary_from,
                salary_to=salary_to,
                currency=currency,
                gross=gross,
                employer=employer_node.get_text(" ", strip=True) if employer_node else None,
                area=area_node.get_text(" ", strip=True) if area_node else None,
                experience=exp_node.get_text(" ", strip=True) if exp_node else None,
                activity_text=activity_node.get_text(" ", strip=True) if activity_node else None,
            ))
        return cards

    @staticmethod
    def _extract_salary(article) -> tuple:
        """Зарплата лежит в элементах <data value="300000">/<data value="RUB">."""
        data_nodes = article.select("data[value]")
        numbers: list[int] = []
        currency = None
        container = None
        for node in data_nodes:
            value = (node.get("value") or "").strip()
            if value.isdigit():
                numbers.append(int(value))
            elif value in _CURRENCY_CODES:
                currency = _CURRENCY_CODES[value]
                container = node.find_parent("span")
        if not numbers:
            return None, None, currency, None
        text = container.get_text(" ", strip=True) if container else ""
        return parse_salary(text, fallback_numbers=numbers, fallback_currency=currency)

    async def fetch_vacancy_datetime(self, hh_id: str) -> datetime | None:
        """Лучший-effort: точная дата публикации со страницы вакансии."""
        url = f"{self.settings.hh_base_url}/vacancy/{hh_id}"
        resp = await self.client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for selector in (
            '[data-qa="vacancy-view-creation-time"]',
            '[itemprop="datePosted"]',
            'time[data-qa="vacancy-view-creation-time"]',
        ):
            node = soup.select_one(selector)
            if node is None:
                continue
            stamp = node.get("datetime") or node.get("content") or node.get_text("", strip=True)
            if stamp:
                try:
                    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    continue
        return None
