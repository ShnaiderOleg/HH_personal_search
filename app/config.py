from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # hh.ru
    hh_base_url: str = "https://hh.ru"
    hh_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    hh_session_cookies: str = ""

    # scraping / polling
    poll_interval_minutes: int = 120
    request_delay_seconds: float = 3.0
    max_pages: int = 3
    scrape_timeout_seconds: float = 30.0

    # telegram
    tg_bot_token: str = ""
    tg_chat_ids: str = ""

    # email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    # db
    db_path: Path = BASE_DIR / "data" / "hh.db"

    # отображение времени (IANA, например Europe/Moscow)
    app_timezone: str = "Europe/Moscow"

    @property
    def tg_chat_id_list(self) -> list[int]:
        return [int(x.strip()) for x in self.tg_chat_ids.split(",") if x.strip()]

    @property
    def cookie_header(self) -> str:
        return self.hh_session_cookies.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
