from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.forms import MoneyField
from apps.taxonomy.models import Category
from apps.taxonomy.selectors import list_categories
from apps.users.models import User

from .models import Account, AccountKind, PaymentMethod, TransactionKind
from .selectors import list_accounts
from .services import TransactionData

# Campos do model/service → campos do formulário (para devolver erros ao lugar certo).
_SERVICE_FIELD_MAP = {"amount_cents": "amount", "__all__": None}


class TransactionForm(forms.Form):
    kind = forms.ChoiceField(
        label="Tipo",
        choices=TransactionKind.choices,
        initial=TransactionKind.EXPENSE,
        widget=forms.RadioSelect,
    )
    amount = MoneyField(label="Valor")
    # Querysets reais (escopados pelo usuário) são definidos em __init__.
    category = forms.ModelChoiceField(
        label="Categoria",
        queryset=Category.objects.none(),
        required=False,
        widget=forms.RadioSelect,
    )
    account = forms.ModelChoiceField(
        label="Conta", queryset=Account.objects.none(), empty_label=None
    )
    destination_account = forms.ModelChoiceField(
        label="Para a conta",
        queryset=Account.objects.none(),
        required=False,
        empty_label="Escolha a conta",
    )
    date = forms.DateField(
        label="Data", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    tags = forms.CharField(
        label="Tags",
        required=False,
        max_length=500,
        help_text="Separe por vírgula. Ex.: restaurante, almoço",
        widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "none"}),
    )
    description = forms.CharField(label="Descrição", required=False, max_length=140)
    payment_method = forms.ChoiceField(
        label="Forma de pagamento",
        required=False,
        choices=[("", "Não informar"), *PaymentMethod.choices],
    )
    notes = forms.CharField(
        label="Observações", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        accounts = list_accounts(user)
        self.fields["account"].queryset = accounts  # type: ignore[attr-defined]
        self.fields["destination_account"].queryset = accounts  # type: ignore[attr-defined]
        self.fields["category"].queryset = list_categories(user)  # type: ignore[attr-defined]
        self.fields["date"].initial = timezone.localdate
        self.fields["amount"].widget.attrs.update({"class": "amount-input", "autofocus": True})
        for name in (
            "account",
            "destination_account",
            "date",
            "tags",
            "description",
            "payment_method",
            "notes",
        ):
            self.fields[name].widget.attrs.setdefault("class", "input")

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        # Campos da outra modalidade podem chegar preenchidos (escondidos na tela): descarta.
        # Os campos obrigatórios por tipo também são checados aqui (além do model) para que
        # todos os erros apareçam de uma vez, junto com os de valor e data.
        if data.get("kind") == TransactionKind.TRANSFER:
            data["category"] = None
            data["payment_method"] = ""
            if not data.get("destination_account") and "destination_account" not in self.errors:
                self.add_error("destination_account", "Informe a conta de destino.")
        else:
            data["destination_account"] = None
            if not data.get("category") and "category" not in self.errors:
                self.add_error("category", "Escolha uma categoria.")
        account: Account | None = data.get("account")
        if (
            data.get("kind") == TransactionKind.EXPENSE
            and account is not None
            and account.kind == AccountKind.CREDIT_CARD
            and not data.get("payment_method")
        ):
            data["payment_method"] = PaymentMethod.CREDIT
        return data

    def tag_names(self) -> list[str]:
        return [name for name in self.cleaned_data.get("tags", "").split(",") if name.strip()]

    def to_data(self) -> TransactionData:
        d = self.cleaned_data
        return TransactionData(
            kind=d["kind"],
            amount_cents=d["amount"],
            date=d["date"],
            account=d["account"],
            category=d.get("category"),
            destination_account=d.get("destination_account"),
            tags=self.tag_names(),
            payment_method=d.get("payment_method", ""),
            description=d.get("description", ""),
            notes=d.get("notes", ""),
        )

    def add_service_errors(self, error: ValidationError) -> None:
        """Erros de regra de negócio (service/model) exibidos no campo correspondente."""
        if not hasattr(error, "error_dict"):
            self.add_error(None, error)
            return
        for field, messages in error.message_dict.items():
            target = _SERVICE_FIELD_MAP.get(field, field)
            self.add_error(target if target in self.fields else None, messages)
