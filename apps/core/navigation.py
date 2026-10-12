"""Itens do menu principal. Páginas ainda não construídas aparecem desabilitadas ("em breve")
e se ativam sozinhas quando a rota passa a existir."""

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class NavItem:
    label: str
    icon: str
    url_name: str
    url: str | None
    active: bool


_ITEMS = (
    ("Início", "layout-dashboard", "home"),
    ("Movimentações", "arrow-left-right", "ledger:transaction_list"),
    ("Categorias", "tags", "taxonomy:index"),
    ("Contas", "landmark", "ledger:account_list"),
    ("Dados", "database", "portability:index"),
)


def _url(name: str) -> str | None:
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def navigation(request: HttpRequest) -> dict[str, Any]:
    if not request.user.is_authenticated:
        return {}
    current = request.resolver_match.view_name if request.resolver_match else ""
    items = [
        NavItem(label, icon, name, _url(name), active=current == name)
        for label, icon, name in _ITEMS
    ]
    return {"nav_items": items, "new_transaction_url": _url("ledger:transaction_create")}
