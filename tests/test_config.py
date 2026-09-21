import pytest
from pydantic import ValidationError

from expense_bot.config import Settings


def make_settings(**values: str) -> Settings:
    defaults = {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "ALLOWED_TELEGRAM_USER_IDS": "42",
    }
    return Settings(_env_file=None, **(defaults | values))


def test_allowed_user_ids_are_parsed_from_comma_separated_setting() -> None:
    settings = make_settings(ALLOWED_TELEGRAM_USER_IDS="42, 100,42")

    assert settings.allowed_user_ids == frozenset({42, 100})


@pytest.mark.parametrize("value", ["", "abc", "42,-1", "42,0"])
def test_invalid_allowed_user_ids_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(ALLOWED_TELEGRAM_USER_IDS=value)
