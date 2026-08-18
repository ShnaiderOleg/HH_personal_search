from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Гарантирует tz-aware datetime в UTC (SQLite хранит naive-время как UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_dt(dt: datetime | None, tz_name: str) -> str:
    """Форматирует datetime в часовом поясе приложения: 'дд.мм чч:мм'."""
    dt = ensure_utc(dt)
    if dt is None:
        return "—"
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%d.%m %H:%M")
