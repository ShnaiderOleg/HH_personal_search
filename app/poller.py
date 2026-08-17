import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .anti_bot import ScraperBlockedError
from .config import Settings
from .db import SessionLocal
from .hh_scraper import HHScraper
from .models import Search, utcnow
from .notifications import email as email_notifier
from .notifications import telegram as telegram_notifier
from .services import vacancy_service

logger = logging.getLogger(__name__)

PAGE_SIZE = 20


class Poller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._scheduler: AsyncIOScheduler | None = None
        self._running = False

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
        except Exception:
            logger.exception("poll cycle failed")
        finally:
            await scraper.aclose()
            self._running = False

    async def _process_search(self, scraper: HHScraper, db, search: Search) -> None:
        new_vacancies = []
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
            search.last_run_at = utcnow()
            search.last_error = None
            db.commit()
        except ScraperBlockedError as exc:
            search.last_error = f"блокировка: {exc}"
            db.commit()
            logger.warning("search %s blocked: %s", search.title, exc)
            return
        except Exception as exc:
            search.last_error = f"{type(exc).__name__}: {exc}"
            db.commit()
            logger.exception("search %s failed", search.title)
            return

        if new_vacancies:
            await self._notify(new_vacancies)

    async def _notify(self, new_vacancies) -> None:
        if self.settings.tg_bot_token and self.settings.tg_chat_id_list:
            await telegram_notifier.send_new_vacancies_telegram(self.settings, new_vacancies)
        if self.settings.smtp_host and self.settings.email_to:
            await asyncio.to_thread(
                email_notifier.send_digest_email, self.settings, new_vacancies
            )
