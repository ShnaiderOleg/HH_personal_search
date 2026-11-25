from app.config import Settings
from app.models import TelegramVacancyMessage, Vacancy
from app.notifications.telegram import _card, process_reaction_update


def test_repeat_card_contains_previous_status():
    vacancy = Vacancy(
        hh_id="new",
        title="Python Developer",
        url="https://hh.ru/vacancy/new",
        employer="Компания",
    )
    vacancy._is_repeat = True
    vacancy._previous_status = "Отказ"

    message = _card(vacancy)

    assert "Повтор" in message
    assert "прошлый статус: <b>Отказ</b>" in message


def test_repeat_card_handles_empty_previous_status():
    vacancy = Vacancy(
        hh_id="new",
        title="Python Developer",
        url="https://hh.ru/vacancy/new",
        employer="Компания",
    )
    vacancy._is_repeat = True
    vacancy._previous_status = None

    message = _card(vacancy)

    assert "прошлый статус: <b>не указан</b>" in message


def _reaction(chat_id: int, message_id: int, emoji: str, user_id: int = 1) -> dict:
    return {
        "message_reaction": {
            "chat": {"id": chat_id},
            "message_id": message_id,
            "user": {"id": user_id},
            "new_reaction": [{"type": "emoji", "emoji": emoji}],
        }
    }


def test_thumb_up_reaction_sets_applied_status(db):
    vacancy = Vacancy(hh_id="123", title="Python", url="https://hh.ru/vacancy/123")
    db.add(vacancy)
    db.flush()
    db.add(TelegramVacancyMessage(vacancy_id=vacancy.id, chat_id=100, message_id=50))
    db.commit()

    status = process_reaction_update(Settings(tg_chat_ids="100"), _reaction(100, 50, "👍"))

    db.refresh(vacancy)
    assert status == "Отклик"
    assert vacancy.status == "Отклик"
    assert vacancy.applied_at is not None


def test_thumb_down_reaction_sets_not_interested(db):
    vacancy = Vacancy(hh_id="124", title="DevOps", url="https://hh.ru/vacancy/124")
    db.add(vacancy)
    db.flush()
    db.add(TelegramVacancyMessage(vacancy_id=vacancy.id, chat_id=100, message_id=51))
    db.commit()

    status = process_reaction_update(Settings(tg_chat_ids="100"), _reaction(100, 51, "👎"))

    db.refresh(vacancy)
    assert status == "не интересует"
    assert vacancy.status == "не интересует"
