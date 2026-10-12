from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.core.forms import MoneyField, apply_service_errors, has_data
from apps.core.money import NBSP, format_brl
from apps.ledger.forms import TransactionForm
from apps.taxonomy.models import Category, CategoryKind
from apps.users.models import User

from .domain.schedule import Frequency
from .models import Budget, InstallmentPlan, Recurrence
from .services import RecurrenceData


class RecurrenceForm(TransactionForm):
    """Mesmos campos da movimentação (o campo `date` vira a primeira data) + calendário."""

    frequency = forms.ChoiceField(
        label="Repetir",
        choices=[
            (Frequency.MONTHLY, "Todo mês"),
            (Frequency.WEEKLY, "Toda semana"),
            (Frequency.YEARLY, "Todo ano"),
        ],
        initial=Frequency.MONTHLY,
    )
    interval = forms.IntegerField(label="A cada", min_value=1, max_value=12, initial=1)
    end_date = forms.DateField(
        label="Até",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Deixe em branco para repetir sem data final.",
    )

    installments = None  # type: ignore[assignment]  # recorrência não é parcelada

    def __init__(
        self, *args: Any, user: User, recurrence: Recurrence | None = None, **kwargs: Any
    ) -> None:
        if recurrence is not None and "initial" not in kwargs and not has_data(args, kwargs):
            kwargs["initial"] = self.initial_from_recurrence(recurrence)
        super().__init__(*args, user=user, **kwargs)
        self.recurrence = recurrence
        self.fields["date"].label = "Primeira data"
        for name in ("frequency", "interval", "end_date"):
            self.fields[name].widget.attrs.setdefault("class", "input")
        self.fields["interval"].widget.attrs.update({"inputmode": "numeric"})

    @staticmethod
    def initial_from_recurrence(rec: Recurrence) -> dict[str, Any]:
        return {
            "kind": rec.kind,
            "amount": format_brl(rec.amount_cents).removeprefix(f"R${NBSP}"),
            "category": rec.category_id,
            "account": rec.account_id,
            "destination_account": rec.destination_account_id,
            "date": rec.start_date,
            "tags": ", ".join(t.name for t in rec.tags.all()),
            "description": rec.description,
            "payment_method": rec.payment_method,
            "notes": rec.notes,
            "frequency": rec.frequency,
            "interval": rec.interval,
            "end_date": rec.end_date,
        }

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        start, end = data.get("date"), data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "A data final deve ser depois da primeira data.")
        return data

    def to_recurrence_data(self) -> RecurrenceData:
        d = self.cleaned_data
        return RecurrenceData(
            entry=self.to_data(),
            frequency=d["frequency"],
            interval=d["interval"],
            start_date=d["date"],
            end_date=d.get("end_date"),
        )

    def add_service_errors(self, error: ValidationError) -> None:
        apply_service_errors(self, error, {"amount_cents": "amount", "start_date": "date"})


class InstallmentPlanForm(forms.Form):
    """O que dá para mudar numa compra parcelada (vale para todas as parcelas)."""

    description = forms.CharField(label="Descrição", max_length=120)
    category = forms.ModelChoiceField(
        label="Categoria", queryset=Category.objects.none(), empty_label=None
    )
    tags = forms.CharField(label="Tags", required=False, max_length=500)
    notes = forms.CharField(
        label="Observações", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args: Any, user: User, plan: InstallmentPlan, **kwargs: Any) -> None:
        if "initial" not in kwargs and not has_data(args, kwargs):
            kwargs["initial"] = {
                "description": plan.description,
                "category": plan.category_id,
                "tags": ", ".join(t.name for t in plan.tags.all()),
                "notes": plan.notes,
            }
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.for_user(user).filter(  # type: ignore[attr-defined]
            Q(kind=CategoryKind.EXPENSE, is_archived=False) | Q(pk=plan.category_id)
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")

    def tag_names(self) -> list[str]:
        return [n for n in self.cleaned_data.get("tags", "").split(",") if n.strip()]


class BudgetForm(forms.Form):
    category = forms.ModelChoiceField(
        label="Categoria",
        queryset=Category.objects.none(),
        required=False,
        empty_label="Todas as despesas (total do mês)",
    )
    amount = MoneyField(label="Limite por mês")

    def __init__(self, *args: Any, user: User, budget: Budget | None = None, **kwargs: Any) -> None:
        if budget is not None and "initial" not in kwargs and not has_data(args, kwargs):
            kwargs["initial"] = {
                "amount": format_brl(budget.amount_cents).removeprefix(f"R${NBSP}")
            }
        super().__init__(*args, **kwargs)
        self.budget = budget
        if budget is not None:
            del self.fields["category"]  # a categoria de um orçamento não muda
        else:
            taken = Budget.objects.for_user(user).filter(category__isnull=False)
            self.fields["category"].queryset = (  # type: ignore[attr-defined]
                Category.objects.for_user(user)
                .filter(kind=CategoryKind.EXPENSE, is_archived=False)
                .exclude(pk__in=taken.values("category_id"))
            )
        self.fields["amount"].widget.attrs.update({"class": "input", "inputmode": "decimal"})
        if "category" in self.fields:
            self.fields["category"].widget.attrs["class"] = "input"
