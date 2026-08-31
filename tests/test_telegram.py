from app.models import Vacancy
from app.notifications.telegram import _card


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
