import datetime
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from apps.core.forms import MoneyField
from apps.core.money import NBSP, format_brl
from apps.taxonomy.models import Category, Tag
from apps.taxonomy.selectors import list_tags
from apps.users.models import User

from .filters import DEFAULT_PERIOD, PERIOD_CHOICES, TransactionFilter, period_range
from .models import Account, AccountKind, PaymentMethod, Transaction, TransactionKind
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

    def __init__(
        self, *args: Any, user: User, instance: Transaction | None = None, **kwargs: Any
    ) -> None:
        if instance is not None and "initial" not in kwargs and not args:
            kwargs["initial"] = self.initial_from(instance)
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance = instance
        # Ativos + os já usados pela movimentação em edição (mesmo que arquivados depois).
        keep_accounts = (
            Q(pk__in=[instance.account_id, instance.destination_account_id]) if instance else Q()
        )
        keep_category = Q(pk=instance.category_id) if instance and instance.category_id else Q()
        accounts = Account.objects.for_user(user).filter(Q(is_archived=False) | keep_accounts)
        self.fields["account"].queryset = accounts  # type: ignore[attr-defined]
        self.fields["destination_account"].queryset = accounts  # type: ignore[attr-defined]
        self.fields["category"].queryset = Category.objects.for_user(user).filter(  # type: ignore[attr-defined]
            Q(is_archived=False) | keep_category
        )
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

    @staticmethod
    def initial_from(tx: Transaction) -> dict[str, Any]:
        return {
            "kind": tx.kind,
            "amount": format_brl(tx.amount_cents).removeprefix(f"R${NBSP}"),
            "category": tx.category_id,
            "account": tx.account_id,
            "destination_account": tx.destination_account_id,
            "date": tx.date,
            "tags": ", ".join(t.name for t in tx.tags.all()),
            "description": tx.description,
            "payment_method": tx.payment_method,
            "notes": tx.notes,
        }

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


class TransactionFilterForm(forms.Form):
    """Lê os filtros da query string (GET). Valores inválidos são simplesmente ignorados."""

    periodo = forms.ChoiceField(label="Período", choices=PERIOD_CHOICES, required=False)
    de = forms.DateField(label="De", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    ate = forms.DateField(
        label="Até", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    tipo = forms.MultipleChoiceField(
        label="Tipo",
        choices=TransactionKind.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    categoria = forms.ModelMultipleChoiceField(
        label="Categorias",
        queryset=Category.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    tag = forms.ModelMultipleChoiceField(
        label="Tags",
        queryset=Tag.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    conta = forms.ModelMultipleChoiceField(
        label="Contas",
        queryset=Account.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    pagamento = forms.MultipleChoiceField(
        label="Forma de pagamento",
        choices=PaymentMethod.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    q = forms.CharField(label="Buscar", required=False, max_length=100)

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["categoria"].queryset = Category.objects.for_user(user)  # type: ignore[attr-defined]
        self.fields["tag"].queryset = list_tags(user)  # type: ignore[attr-defined]
        self.fields["conta"].queryset = Account.objects.for_user(user)  # type: ignore[attr-defined]
        self.fields["q"].widget.attrs.update(
            {"class": "input", "placeholder": "Buscar na descrição", "type": "search"}
        )
        for name in ("periodo", "de", "ate"):
            self.fields[name].widget.attrs.setdefault("class", "input")

    def period(self) -> str:
        data = self.cleaned_data if hasattr(self, "cleaned_data") else {}
        if data.get("de") or data.get("ate"):
            return "personalizado"
        return data.get("periodo") or DEFAULT_PERIOD

    def to_filter(self, today: datetime.date) -> TransactionFilter:
        self.is_valid()  # popula cleaned_data só com os campos válidos
        data = self.cleaned_data
        period = self.period()
        if period == "personalizado":
            date_from, date_to = data.get("de"), data.get("ate")
        else:
            date_from, date_to = period_range(period, today)
        return TransactionFilter(
            date_from=date_from,
            date_to=date_to,
            kinds=tuple(data.get("tipo") or ()),
            category_ids=tuple(c.pk for c in data.get("categoria") or ()),
            tag_ids=tuple(t.pk for t in data.get("tag") or ()),
            account_ids=tuple(a.pk for a in data.get("conta") or ()),
            payment_methods=tuple(data.get("pagamento") or ()),
            search=(data.get("q") or "").strip(),
        )
