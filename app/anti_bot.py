class ScraperBlockedError(Exception):
    """hh.ru заблокировал запрос (капча, 403/429/503) или сеть недоступна."""


class ParsingError(Exception):
    """Не удалось распарсить страницу."""
