from typing import Any

from django import forms

from apps.core.forms import MoneyField, has_data
from apps.core.money import NBSP, format_brl
from apps.ledger.models import Account, AccountKind
from apps.users.models import User

from .models import Goal


class GoalForm(forms.Form):
    name = forms.CharField(label="Nome", max_length=80)
    target = MoneyField(label="Valor-alvo")
    account = forms.ModelChoiceField(
        label="Conta onde o dinheiro fica", queryset=Account.objects.none(), empty_label=None
    )
    deadline = forms.DateField(
        label="Prazo",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Opcional. Sem prazo, o Moneta estima quando você chega lá no ritmo atual.",
    )

    def __init__(self, *args: Any, user: User, goal: Goal | None = None, **kwargs: Any) -> None:
        if goal is not None and "initial" not in kwargs and not has_data(args, kwargs):
            kwargs["initial"] = {
                "name": goal.name,
                "target": format_brl(goal.target_cents).removeprefix(f"R${NBSP}"),
                "account": goal.account_id,
                "deadline": goal.deadline,
            }
        super().__init__(*args, **kwargs)
        taken = Goal.objects.for_user(user).values("account_id")
        if goal is not None:
            taken = taken.exclude(pk=goal.pk)
        self.fields["account"].queryset = (  # type: ignore[attr-defined]
            Account.objects.for_user(user)
            .exclude(kind=AccountKind.CREDIT_CARD)
            .exclude(pk__in=taken)
            .filter(is_archived=False)
            .order_by("-kind", "name")
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")
