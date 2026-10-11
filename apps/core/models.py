"""Modelos base compartilhados por todos os apps."""

import uuid
from typing import Self

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OwnedQuerySet[M: models.Model](models.QuerySet[M]):
    def for_user(self, user: AbstractBaseUser | AnonymousUser) -> Self:
        """Único ponto de leitura de dados do usuário: sempre escopa por dono."""
        if not user.is_authenticated:
            return self.none()
        return self.filter(owner=user)


class OwnedModel(TimeStampedModel):
    """Base de todo dado pertencente a um usuário.

    UUID v7 como PK: opaco nas URLs, ordenável no tempo e estável em export/import.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        db_index=True,
    )

    objects = OwnedQuerySet.as_manager()

    class Meta:
        abstract = True
