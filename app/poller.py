import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .anti_bot import ScraperBlockedError
from .ai import AIClient, get_ai_model
from .config import Settings
from .db import SessionLocal
from .hh_scraper import HHScraper
from .models import Search, utcnow
from .notifications import email as email_notifier
from .notifications import telegram as telegram_notifier
from .services import vacancy_service

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
BLOCKED_COOLDOWN_MINUTES = 60
BLOCKED_MAX_COOLDOWN_MINUTES = 480


class Poller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._scheduler: AsyncIOScheduler | None = None
        self._running = False
        self._blocked_until: dict[int, datetime] = {}
        self._block_failures: dict[int, int] = {}
        self._alerted: dict[int | str, str] = {}

    def start(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self.run_once,
            "interval",
            minutes=self.settings.poll_interval_minutes,
            id="hh_poll",
            max_instances=1,
        )
        self._scheduler.start()
        asyncio.get_running_loop().create_task(self.run_once())

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)

    async def run_once(self) -> None:
        if self._running:
            return
        self._running = True
        scraper = HHScraper(self.settings)
        try:
            db = SessionLocal()
            try:
                searches = db.query(Search).filter(Search.active.is_(True)).all()
                for search in searches:
                    await self._process_search(scraper, db, search)
            finally:
                db.close()
        except Exception as exc:
            logger.exception("poll cycle failed")
            await self._send_alert("cycle", f"Ошибка цикла поллинга: {exc!r}")
        finally:
            await scraper.aclose()
            self._running = False

    async def _process_search(self, scraper: HHScraper, db, search: Search) -> None:
        now = utcnow()
        if now < self._blocked_until.get(search.id, now):
            return
        new_vacancies = []
        error_msg = None
        try:
            for page in range(self.settings.max_pages):
                html = await scraper.fetch_serp(
                    search.query, search.area_id, page=page, title_only=search.title_only
                )
                cards = scraper.parse_serp(html)
                if not cards:
                    break
                fresh = vacancy_service.ingest_vacancies(db, search, cards)
                new_vacancies.extend(fresh)
                if len(cards) < PAGE_SIZE:
                    break
                await asyncio.sleep(self.settings.request_delay_seconds)
        except ScraperBlockedError as exc:
            error_msg = f"блокировка: {exc}"
            fails = self._block_failures.get(search.id, 0) + 1
            self._block_failures[search.id] = fails
            cooldown = min(
                BLOCKED_COOLDOWN_MINUTES * (2 ** (fails - 1)),
                BLOCKED_MAX_COOLDOWN_MINUTES,
            )
            self._blocked_until[search.id] = utcnow() + timedelta(minutes=cooldown)
            logger.warning(
                "search %s blocked (%s in a row), cooldown %s min: %s",
                search.title, fails, cooldown, exc,
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception("search %s failed", search.title)
        else:
            self._block_failures[search.id] = 0

        search.last_run_at = utcnow()
        search.last_error = error_msg
        db.commit()

        if error_msg:
            await self._send_alert(search.id, f"Поиск «{search.title}»: {error_msg}")
        else:
            self._alerted.pop(search.id, None)

        if new_vacancies:
            await self._score_new_vacancies(db, search, new_vacancies)
            await self._notify(new_vacancies)

    async def _score_new_vacancies(self, db, search: Search, vacancies) -> None:
        """Оценивает новые вакансии нейросетью по резюме из поиска."""
        resume_url = (search.resume_url or "").strip()
        model_id = (search.ai_model or "").strip()
        if not resume_url or not model_id:
            return
        spec = get_ai_model(model_id)
        if spec is None:
            logger.warning("AI-модель '%s' не найдена в списке, оценка пропущена", model_id)
            return
        settings = self.settings
        if spec["provider"] == "gigachat" and not settings.gigachat_auth_key:
            logger.warning("GigaChat не настроен (GIGACHAT_AUTH_KEY пуст), оценка пропущена")
            return
        if spec["provider"] == "proxyapi" and not settings.proxyapi_api_key:
            logger.warning("ProxyAPI не настроен (PROXYAPI_API_KEY пуст), оценка пропущена")
            return
        client = AIClient(settings)
        try:
            resume_text = await client.fetch_resume_text(resume_url)
        except Exception as exc:
            logger.warning(
                "Не удалось загрузить резюме для поиска '%s': %s", search.title, exc
            )
            return
        if not resume_text.strip():
            logger.warning("Резюме для поиска '%s' пустое", search.title)
            return
        scored = 0
        for vacancy in vacancies:
            try:
                score = await client.score_vacancy(spec, resume_text, vacancy)
                if score is not None:
                    vacancy.match_score = score
                    scored += 1
            except Exception as exc:
                logger.warning("Оценка вакансии %s не удалась: %s", vacancy.hh_id, exc)
            await asyncio.sleep(settings.ai_request_delay_seconds)
        if scored:
            db.commit()
            logger.info("Оценено соответствие для %s/%s новых вакансий поиска '%s'", scored, len(vacancies), search.title)

    async def _send_alert(self, key: int | str, text: str) -> None:
        """Шлёт алерт в Telegram, не повторяя одинаковый текст для одного ключа."""
        if not text or self._alerted.get(key) == text:
            return
        await telegram_notifier.send_error_alert_telegram(self.settings, text)
        self._alerted[key] = text

    async def _notify(self, new_vacancies) -> None:
        if self.settings.tg_bot_token and self.settings.tg_chat_id_list:
            await telegram_notifier.send_new_vacancies_telegram(self.settings, new_vacancies)
        if self.settings.smtp_host and self.settings.email_to:
            await asyncio.to_thread(
                email_notifier.send_digest_email, self.settings, new_vacancies
            )
