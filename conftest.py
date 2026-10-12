from typing import Any

import pytest


@pytest.fixture
def user(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(username="ana", password="senha-forte-123")


@pytest.fixture
def other_user(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(username="bruno", password="senha-forte-123")
