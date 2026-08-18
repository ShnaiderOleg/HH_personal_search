import json
import logging
import re
import time
import uuid

import httpx
from bs4 import BeautifulSoup

from .config import BASE_DIR, Settings
from .services.vacancy_service import salary_label

logger = logging.getLogger(__name__)

_DEFAULT_MODELS = [
    {"provider": "gigachat", "model": "GigaChat-3-Ultra", "label": "GigaChat 3 Ultra"},
    {"provider": "gigachat", "model": "GigaChat-2-Max", "label": "GigaChat 2 Max"},
    {"provider": "gigachat", "model": "GigaChat-2-Pro", "label": "GigaChat 2 Pro"},
    {"provider": "gigachat", "model": "GigaChat-2", "label": "GigaChat 2"},
    {"provider": "proxyapi", "model": "openai/gpt-5.6-luna", "label": "GPT-5.6 Luna (OpenAI)"},
    {"provider": "proxyapi", "model": "openai/gpt-5.5", "label": "GPT-5.5 (OpenAI)"},
    {"provider": "proxyapi", "model": "openai/gpt-5.4-mini", "label": "GPT-5.4 Mini (OpenAI)"},
    {"provider": "proxyapi", "model": "anthropic/claude-sonnet-5", "label": "Claude Sonnet 5 (Anthropic)"},
    {"provider": "proxyapi", "model": "anthropic/claude-opus-5", "label": "Claude Opus 5 (Anthropic)"},
    {"provider": "proxyapi", "model": "anthropic/claude-haiku-4-5", "label": "Claude Haiku 4.5 (Anthropic)"},
    {"provider": "proxyapi", "model": "gemini/gemini-3.7-flash", "label": "Gemini 3.7 Flash (Google)"},
    {"provider": "proxyapi", "model": "gemini/gemini-2.5-pro", "label": "Gemini 2.5 Pro (Google)"},
    {"provider": "proxyapi", "model": "gemini/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite (Google)"},
]

SCORE_SYSTEM_PROMPT = (
    "Ты — рекрутер, оценивающий соответствие кандидата вакансии. "
    "Проанализируй резюме кандидата и текст вакансии. "
    "Оцени, насколько кандидат подходит вакансии, по 5-балльной шкале: "
    "1 — совсем не подходит, 2 — слабое соответствие, 3 — среднее, "
    "4 — хорошо подходит, 5 — идеальное соответствие. "
    "Учитывай требования к опыту, навыкам, технологиям, образованию, региону. "
    "Ответь СТРОГО одним объектом JSON без пояснений вне него: "
    '{"score": <целое число от 1 до 5>, "reason": "<краткое обоснование на русском, до 2 предложений>"}'
)

_SCORE_RE = re.compile(r'"score"\s*:\s*([1-5])')


def load_ai_models() -> list[dict]:
    """Загружает список моделей из ai_models.json, при ошибке — встроенный список."""
    path = BASE_DIR / "ai_models.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            logger.warning("ai_models.json повреждён, использую встроенный список")
    return list(_DEFAULT_MODELS)


def get_ai_model(model_id: str) -> dict | None:
    for spec in load_ai_models():
        if spec.get("model") == model_id:
            return spec
    return None


def parse_score(content: str | None) -> int | None:
    if not content:
        return None
    m = _SCORE_RE.search(content)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([1-5])\b", content)
    if m:
        return int(m.group(1))
    return None


class AIClient:
    """Оценка вакансий нейросетями: GigaChat (Sber) и ProxyAPI (OpenAI-совместимый)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._giga_token: str | None = None
        self._giga_token_expires: float = 0.0

    @staticmethod
    def _http_client(timeout: float = 60.0, verify: bool = True) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=verify)

    async def _gigachat_access_token(self) -> str:
        if self._giga_token and time.time() < self._giga_token_expires - 60:
            return self._giga_token
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {self.settings.gigachat_auth_key}",
        }
        async with self._http_client(verify=False) as client:
            resp = await client.post(
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                headers=headers,
                data={"scope": "GIGACHAT_API_PERS"},
            )
            resp.raise_for_status()
            payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("GigaChat: пустой access_token в ответе OAuth")
        self._giga_token = token
        self._giga_token_expires = time.time() + 30 * 60
        return token

    async def _chat_completion(
        self, spec: dict, messages: list[dict], temperature: float = 0.2, max_tokens: int = 300
    ) -> str:
        provider = spec.get("provider")
        if provider == "gigachat":
            return await self._giga_chat(spec["model"], messages, temperature, max_tokens)
        if provider == "proxyapi":
            return await self._proxyapi_chat(spec["model"], messages, temperature, max_tokens)
        raise ValueError(f"неизвестный провайдер модели: {provider}")

    async def _giga_chat(self, model: str, messages: list[dict], temperature: float, max_tokens: int) -> str:
        token = await self._gigachat_access_token()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with self._http_client(verify=False) as client:
            resp = await client.post(
                "https://api.giga.chat/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def _proxyapi_chat(self, model: str, messages: list[dict], temperature: float, max_tokens: int) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.proxyapi_api_key}",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with self._http_client() as client:
            resp = await client.post(
                f"{self.settings.proxyapi_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def fetch_resume_text(self, url: str, max_chars: int = 12000) -> str:
        url = url.strip()
        headers = {
            "User-Agent": self.settings.hh_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if "disk.yandex.ru" in url:
            url = await self._resolve_yandex_disk(url)
        if "hh.ru" in url and self.settings.cookie_header:
            headers["Cookie"] = self.settings.cookie_header
        if "docs.google.com/document/" in url:
            url = url.rstrip("/") + "/export?format=txt"
        async with self._http_client(30.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()
        filename = url.rsplit("/", 1)[-1].split("?")[0].lower()
        if "pdf" in ctype or filename.endswith(".pdf"):
            return self._pdf_text(resp.content)[:max_chars]
        if "docx" in ctype or filename.endswith(".docx"):
            return self._docx_text(resp.content)[:max_chars]
        if "text/plain" in ctype or filename.endswith((".txt", ".md")):
            return resp.text[:max_chars]
        soup = BeautifulSoup(resp.text, "lxml")
        return soup.get_text(" ", strip=True)[:max_chars]

    @staticmethod
    async def _resolve_yandex_disk(url: str) -> str:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://cloud-api.yandex.net/v1/disk/public/resources/download",
                params={"public_key": url},
            )
            resp.raise_for_status()
            href = resp.json().get("href")
        if not href:
            raise RuntimeError("Яндекс.Диск: не удалось получить прямую ссылку на файл")
        return href

    @staticmethod
    def _pdf_text(content: bytes) -> str:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    @staticmethod
    def _docx_text(content: bytes) -> str:
        from io import BytesIO

        from docx import Document

        doc = Document(BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)

    def build_vacancy_prompt(self, vacancy) -> str:
        return (
            f"Название: {vacancy.title}\n"
            f"Работодатель: {vacancy.employer or '—'}\n"
            f"Регион: {vacancy.area or '—'}\n"
            f"Опыт: {vacancy.experience or '—'}\n"
            f"Зарплата: {salary_label(vacancy)}\n"
            f"Ссылка: {vacancy.url}\n"
        )

    async def score_vacancy(self, spec: dict, resume_text: str, vacancy) -> int | None:
        vacancy_text = self.build_vacancy_prompt(vacancy)
        messages = [
            {"role": "system", "content": SCORE_SYSTEM_PROMPT},
            {"role": "user", "content": f"РЕЗЮМЕ КАНДИДАТА:\n{resume_text}\n\nВАКАНСИЯ:\n{vacancy_text}"},
        ]
        content = await self._chat_completion(spec, messages)
        return parse_score(content)
