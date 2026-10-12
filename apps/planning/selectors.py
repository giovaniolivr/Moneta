from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, Min, Q, QuerySet, Sum

from apps.ledger.models import TransactionStatus

from .models import InstallmentPlan, Recurrence

UserLike = AbstractBaseUser | AnonymousUser


def list_recurrences(user: UserLike) -> QuerySet[Recurrence]:
    return (
        Recurrence.objects.for_user(user)
        .select_related("account", "destination_account", "category")
        .prefetch_related("tags")
    )


def list_installment_plans(user: UserLike) -> QuerySet[InstallmentPlan]:
    """Compras parceladas com o andamento: parcelas pagas, quanto falta e a próxima data."""
    planned = Q(transactions__status=TransactionStatus.PLANNED)
    return (
        InstallmentPlan.objects.for_user(user)
        .select_related("account", "category")
        .prefetch_related("tags")
        .annotate(
            paid=Count("transactions", filter=Q(transactions__status=TransactionStatus.POSTED)),
            remaining_cents=Sum("transactions__amount_cents", filter=planned),
            next_date=Min("transactions__date", filter=planned),
        )
    )
