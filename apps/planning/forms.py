from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from apps.core.forms import apply_service_errors, has_data
from apps.core.money import NBSP, format_brl
from apps.ledger.forms import TransactionForm
from apps.users.models import User

from .domain.schedule import Frequency
from .models import Recurrence
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
