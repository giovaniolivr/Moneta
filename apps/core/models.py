"""Modelos base compartilhados por todos os apps."""

import uuid
from typing import Any, Self, TypeVar

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.core.exceptions import ValidationError
from django.db import models

from .names import InvalidNameError, clean_display_name, normalize_name


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


_M = TypeVar("_M", bound=models.Model)


# TypeVar em vez da sintaxe PEP 695: o plugin do django-stubs não resolve o manager com ela.
class OwnedQuerySet(models.QuerySet[_M]):
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


class NormalizedNameModel(models.Model):
    """Nome de exibição livre + chave normalizada (sem caixa/acentos) para unicidade e busca.

    Subclasses declaram a `UniqueConstraint` sobre `normalized_name` no escopo adequado.
    """

    name = models.CharField("nome", max_length=60)
    normalized_name = models.CharField(max_length=60, editable=False, blank=True)  # calculado

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalize()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        try:
            self.normalize()
        except InvalidNameError as exc:
            raise ValidationError({"name": str(exc)}) from exc

    def normalize(self) -> None:
        self.name = clean_display_name(self.name)
        self.normalized_name = normalize_name(self.name)
