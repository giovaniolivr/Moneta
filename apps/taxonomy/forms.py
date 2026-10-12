from typing import Any

from django import forms

from apps.core.forms import has_data as _has_data
from apps.users.models import User

from .icons import CATEGORY_ICONS
from .models import Category, CategoryKind, ColorSlot, Tag


class CategoryForm(forms.Form):
    name = forms.CharField(label="Nome", max_length=60)
    kind = forms.ChoiceField(
        label="Tipo", choices=CategoryKind.choices, widget=forms.RadioSelect, initial="expense"
    )
    color = forms.ChoiceField(
        label="Cor", choices=ColorSlot.choices, widget=forms.RadioSelect, initial=ColorSlot.C1
    )
    icon = forms.ChoiceField(
        label="Ícone", choices=CATEGORY_ICONS, widget=forms.RadioSelect, initial="wallet"
    )

    def __init__(self, *args: Any, instance: Category | None = None, **kwargs: Any) -> None:
        if instance is not None and "initial" not in kwargs and not _has_data(args, kwargs):
            kwargs["initial"] = {
                "name": instance.name,
                "kind": instance.kind,
                "color": instance.color,
                "icon": instance.icon or "wallet",
            }
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["name"].widget.attrs.update({"class": "input", "autofocus": True})
        if instance is not None:
            # O tipo não muda depois de criada (mudaria o sentido das movimentações).
            self.fields["kind"].required = False
            self.fields["kind"].disabled = True


class TagRenameForm(forms.Form):
    name = forms.CharField(label="Nome", max_length=60)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"class": "input"})


class TagAliasForm(forms.Form):
    alias = forms.CharField(
        label="Novo apelido",
        max_length=60,
        help_text="Ex.: “supermercado” passa a significar esta tag.",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["alias"].widget.attrs.update({"class": "input", "autocomplete": "off"})


class TagMergeForm(forms.Form):
    target = forms.ModelChoiceField(
        label="Mesclar nesta tag", queryset=Tag.objects.none(), empty_label="Escolha a tag"
    )

    def __init__(self, *args: Any, user: User, source: Tag, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Tag.objects.for_user(user).exclude(pk=source.pk)  # type: ignore[attr-defined]
        self.fields["target"].widget.attrs.update({"class": "input"})
