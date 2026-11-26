import asyncio
import html
import logging

import httpx

from ..config import Settings
from ..db import SessionLocal
from ..models import TelegramVacancyMessage, Vacancy, utcnow
from ..services.vacancy_service import salary_label

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


def _card(vacancy: Vacancy) -> str:
    parts = [
        f"<b>{html.escape(vacancy.title)}</b>",
    ]
    if getattr(vacancy, "_is_repeat", False):
        previous_status = getattr(vacancy, "_previous_status", None) or "не указан"
        parts.append(
            f"\U0001f501 Повтор — прошлый статус: <b>{html.escape(previous_status)}</b>"
        )
    parts.append(f"\U0001f4b0 {html.escape(salary_label(vacancy))}")
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
                        _API.format(token=settings.tg_bot_token, method="sendMessage"),
                        data={
                            "chat_id": chat_id,
                            "text": _card(vacancy),
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        },
                    )
                    resp.raise_for_status()
                    message_id = resp.json().get("result", {}).get("message_id")
                    if isinstance(message_id, int):
                        _save_message(vacancy.id, chat_id, message_id)
                    sent += 1
                except httpx.HTTPError as exc:
                    logger.warning(
                        "telegram send to %s failed (%s)", chat_id, type(exc).__name__
                    )
    return sent


async def send_error_alert_telegram(settings: Settings, text: str) -> int:
    """Отправляет алерт об ошибке во все чаты. Возвращает число отправленных."""
    if not settings.tg_bot_token or not settings.tg_chat_id_list or not text:
        return 0
    sent = 0
    body = f"\u26a0\ufe0f Ошибка HHSearch:\n{text}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        for chat_id in settings.tg_chat_id_list:
            try:
                resp = await client.post(
                    _API.format(token=settings.tg_bot_token, method="sendMessage"),
                    data={
                        "chat_id": chat_id,
                        "text": body,
                        "disable_web_page_preview": True,
                    },
                )
                resp.raise_for_status()
                sent += 1
            except httpx.HTTPError as exc:
                logger.warning(
                    "telegram alert to %s failed (%s)", chat_id, type(exc).__name__
                )
    return sent


def _save_message(vacancy_id: int | None, chat_id: int, message_id: int) -> None:
    if vacancy_id is None:
        return
    db = SessionLocal()
    try:
        exists = (
            db.query(TelegramVacancyMessage)
            .filter(
                TelegramVacancyMessage.chat_id == chat_id,
                TelegramVacancyMessage.message_id == message_id,
            )
            .first()
        )
        if not exists:
            db.add(
                TelegramVacancyMessage(
                    vacancy_id=vacancy_id, chat_id=chat_id, message_id=message_id
                )
            )
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("не удалось сохранить связь Telegram-сообщения с вакансией")
    finally:
        db.close()


def process_reaction_update(settings: Settings, update: dict) -> str | None:
    """Применяет 👍/👎 из update Telegram. Возвращает новый статус или None."""
    reaction = update.get("message_reaction")
    if not isinstance(reaction, dict):
        return None
    chat_id = reaction.get("chat", {}).get("id")
    message_id = reaction.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return None
    if chat_id not in settings.tg_chat_id_list:
        return None

    emojis = {
        item.get("emoji")
        for item in reaction.get("new_reaction", [])
        if isinstance(item, dict) and item.get("type") == "emoji"
    }
    status = "Отклик" if "👍" in emojis else "не интересует" if "👎" in emojis else None
    if status is None:
        return None

    db = SessionLocal()
    try:
        message = (
            db.query(TelegramVacancyMessage)
            .filter(
                TelegramVacancyMessage.chat_id == chat_id,
                TelegramVacancyMessage.message_id == message_id,
            )
            .first()
        )
        if message is None:
            return None
        vacancy = db.get(Vacancy, message.vacancy_id)
        if vacancy is None:
            return None
        vacancy.status = status
        if status == "Отклик":
            vacancy.applied_at = utcnow()
        db.commit()
        logger.info("Telegram-реакция установила статус %s для вакансии %s", status, vacancy.hh_id)
        return status
    except Exception:
        db.rollback()
        logger.exception("не удалось обработать реакцию Telegram")
        return None
    finally:
        db.close()


def _retry_after_seconds(response: httpx.Response, fallback: int) -> int:
    """Возвращает безопасную задержку после ответа Telegram об ограничении."""
    retry_after = None
    try:
        payload = response.json()
        retry_after = payload.get("parameters", {}).get("retry_after")
    except (TypeError, ValueError):
        pass
    if retry_after is None:
        retry_after = response.headers.get("Retry-After")
    try:
        return max(1, min(int(retry_after), 3600))
    except (TypeError, ValueError):
        return fallback


class TelegramReactionPoller:
    """Long-polling обновлений Telegram без публичного webhook."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._task = None
        self._stopping = False

    def start(self) -> None:
        if not self.settings.tg_bot_token or not self.settings.tg_chat_id_list:
            return
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        offset = None
        retry_delay = 5
        async with httpx.AsyncClient(timeout=40.0) as client:
            while not self._stopping:
                try:
                    data = {
                        "timeout": 30,
                        "allowed_updates": '["message_reaction"]',
                    }
                    if offset is not None:
                        data["offset"] = offset
                    response = await client.post(
                        _API.format(token=self.settings.tg_bot_token, method="getUpdates"),
                        data=data,
                    )
                    if response.is_error:
                        delay = _retry_after_seconds(response, retry_delay)
                        logger.warning(
                            "опрос реакций Telegram: HTTP %s; повтор через %s с",
                            response.status_code,
                            delay,
                        )
                        retry_delay = min(max(retry_delay * 2, delay), 300)
                        await asyncio.sleep(delay)
                        continue
                    payload = response.json()
                    if not payload.get("ok", False):
                        logger.warning("опрос реакций Telegram: API отклонил запрос")
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 300)
                        continue
                    retry_delay = 5
                    for update in payload.get("result", []):
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            offset = update_id + 1
                        process_reaction_update(self.settings, update)
                except asyncio.CancelledError:
                    raise
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning(
                        "опрос реакций Telegram не удался (%s); повтор через %s с",
                        type(exc).__name__,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 300)


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
