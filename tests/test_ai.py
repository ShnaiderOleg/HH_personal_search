from app.ai import AIClient, get_ai_model, load_ai_models, parse_score
from app.config import get_settings

import httpx
import pytest


def test_load_ai_models_has_providers():
    models = load_ai_models()
    assert models
    providers = {m["provider"] for m in models}
    assert "gigachat" in providers
    assert "proxyapi" in providers
    assert "ollama" in providers
    for m in models:
        assert m["model"]
        assert m["label"]
        assert m["provider"] in ("gigachat", "proxyapi", "ollama")


def test_get_ai_model_finds_and_misses():
    models = load_ai_models()
    assert get_ai_model(models[0]["model"]) == models[0]
    assert get_ai_model("несуществующая-модель") is None


def test_parse_score():
    assert parse_score('{"score": 4, "reason": "хорошо"}') == 4
    assert parse_score('{"score":5}') == 5
    assert parse_score("Оценка: 3") == 3
    assert parse_score("5 из 5") == 5
    assert parse_score("ответ: оценка 0") is None
    assert parse_score("") is None
    assert parse_score(None) is None


@pytest.mark.asyncio
async def test_ollama_chat_returns_content(monkeypatch):
    async def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert "ollama_base_url" not in str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"score": 5}'}}]})

    client = AIClient(get_settings())

    def fake_http_client(timeout=60.0, verify=True):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(AIClient, "_http_client", staticmethod(fake_http_client))
    result = await client._ollama_chat("deepseek-r1:7b", [{"role": "user", "content": "hi"}], 0.2)
    assert result == '{"score": 5}'

    spec = get_ai_model("deepseek-r1:7b")
    assert spec is not None and spec["provider"] == "ollama"