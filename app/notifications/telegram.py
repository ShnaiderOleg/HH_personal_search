import html
import logging

import httpx

from ..config import Settings
from ..models import Vacancy
from ..services.vacancy_service import salary_label

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


def _card(vacancy: Vacancy) -> str:
    parts = [
        f"<b>{html.escape(vacancy.title)}</b>",
        f"\U0001f4b0 {html.escape(salary_label(vacancy))}",
    ]
    if vacancy.employer:
        parts.append(f"\U0001f3e2 {html.escape(vacancy.employer)}")
    if vacancy.area:
        parts.append(f"\U0001f4cd {html.escape(vacancy.area)}")
    if vacancy.match_score is not None:
        parts.append(f"\U0001f3af Оценка соответствия: {vacancy.match_score}/5")
    parts.append(f"\U0001f517 <a href=\"{html.escape(vacancy.url)}\">Открыть вакансию</a>")
    return "\n".join(parts)


async def send_new_vacancies_telegram(settings: Settings, vacancies: list[Vacancy]) -> int:
    """Отправляет карточки новых вакансий во все чаты. Возвращает число отправленных."""
    if not settings.tg_bot_token or not settings.tg_chat_id_list or not vacancies:
        return 0
    sent = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        for chat_id in settings.tg_chat_id_list:
            for vacancy in vacancies:
                try:
                    resp = await client.post(
                        _API.format(token=settings.tg_bot_token),
                        data={
                            "chat_id": chat_id,
                            "text": _card(vacancy),
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        },
                    )
                    resp.raise_for_status()
                    sent += 1
                except httpx.HTTPError as exc:
                    logger.warning("telegram send to %s failed: %s", chat_id, exc)
    return sent


async def send_test_telegram(settings: Settings) -> int:
    return await send_new_vacancies_telegram(
        settings,
        [
            Vacancy(
                hh_id="test",
                title="Тестовое сообщение",
                url=f"{settings.hh_base_url}",
                employer="HHSearch",
                area="мониторинг",
                salary_from=100_000,
                salary_to=150_000,
                currency="RUR",
            )
        ],
    )
