import datetime

from django.core.exceptions import ValidationError

from apps.ledger.models import Account
from apps.users.models import User

from .models import Goal


def _save(goal: Goal) -> Goal:
    if goal.account_id and goal.account.owner_id != goal.owner_id:
        raise ValidationError({"account": "Conta inválida."})
    if goal.account_id and goal.account.is_liability:
        raise ValidationError({"account": "Use uma conta onde o dinheiro fica guardado."})
    goal.name = goal.name.strip()
    goal.full_clean()
    goal.save()
    return goal


def create_goal(
    user: User,
    *,
    name: str,
    target_cents: int,
    account: Account,
    deadline: datetime.date | None = None,
) -> Goal:
    return _save(
        Goal(owner=user, name=name, target_cents=target_cents, account=account, deadline=deadline)
    )


def update_goal(
    goal: Goal,
    *,
    name: str,
    target_cents: int,
    account: Account,
    deadline: datetime.date | None,
) -> Goal:
    goal.name, goal.target_cents, goal.account, goal.deadline = (
        name,
        target_cents,
        account,
        deadline,
    )
    return _save(goal)


def delete_goal(goal: Goal) -> None:
    goal.delete()
