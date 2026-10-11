from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Usuário customizado desde a primeira migração, para poder evoluir sem dor
    (perfil, preferências, multiusuário)."""

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"
