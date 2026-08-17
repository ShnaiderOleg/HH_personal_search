import time

import httpx

from .config import Settings

_cache: dict = {"fetched_at": 0.0, "tree": None}
_TTL = 60 * 60 * 24  # сутки

_HEADERS = {
    "User-Agent": "HHSearch/1.0 (personal job monitoring)",
    "Accept": "application/json",
}


def _fetch_tree(settings: Settings) -> list[dict]:
    if _cache["tree"] and (time.time() - _cache["fetched_at"]) < _TTL:
        return _cache["tree"]
    resp = httpx.get(f"{settings.hh_base_url.replace('hh.ru', 'api.hh.ru')}/areas",
                     headers=_HEADERS, timeout=20.0)
    resp.raise_for_status()
    _cache["tree"] = resp.json()
    _cache["fetched_at"] = time.time()
    return _cache["tree"]


def _flatten(nodes: list[dict], path: str = "") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for node in nodes:
        name = node.get("name", "")
        full = f"{path}, {name}".strip(", ")
        result.append((str(node.get("id", "")), full))
        children = node.get("areas") or []
        if children:
            result.extend(_flatten(children, full))
    return result


def search_areas(settings: Settings, query: str, limit: int = 25) -> list[dict]:
    tree = _fetch_tree(settings)
    flat = _flatten(tree)
    q = query.strip().lower()
    if q:
        flat = [item for item in flat if q in item[1].lower()]
    return [
        {"id": area_id, "name": name}
        for area_id, name in flat[:limit]
    ]
