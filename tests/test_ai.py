from app.ai import get_ai_model, load_ai_models, parse_score


def test_load_ai_models_has_providers():
    models = load_ai_models()
    assert models
    providers = {m["provider"] for m in models}
    assert "gigachat" in providers
    assert "proxyapi" in providers
    for m in models:
        assert m["model"]
        assert m["label"]
        assert m["provider"] in ("gigachat", "proxyapi")


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