from django.conf import settings
from django.contrib.auth import get_user_model


def test_custom_user_model_is_active() -> None:
    assert settings.AUTH_USER_MODEL == "users.User"
    assert get_user_model()._meta.label == "users.User"


def test_passwords_use_argon2_outside_tests() -> None:
    from config.settings import base

    assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")
