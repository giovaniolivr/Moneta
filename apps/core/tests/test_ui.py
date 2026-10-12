import datetime
import re
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from apps.ledger.models import TransactionKind
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.taxonomy.models import Category

TEMPLATE_DIRS = [
    Path(settings.BASE_DIR) / "templates",
    *Path(settings.BASE_DIR).glob("apps/*/templates"),
]
TEMPLATES = sorted(p for d in TEMPLATE_DIRS for p in d.rglob("*.html"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_templates_respect_csp(path: Path) -> None:
    """A CSP bloqueia estilos/handlers inline: eles quebrariam a página silenciosamente."""
    html = path.read_text(encoding="utf-8")
    assert not re.search(r"\sstyle=", html), 'use classes em vez de style=""'
    assert not re.search(r"\son[a-z]+=", html), "use addEventListener em um .js"
    for tag in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html):
        assert "nonce=" in tag, f"script inline sem nonce: {tag}"


def test_icon_tag() -> None:
    out = Template(
        '{% load ui %}{% icon "wallet" "icon-sm" %}|{% icon "x" label="Fechar" %}'
    ).render(Context())
    decorative, labelled = out.split("|")
    assert 'class="icon icon-sm"' in decorative
    assert 'aria-hidden="true"' in decorative
    assert "sprite.svg#wallet" in decorative
    assert 'role="img" aria-label="Fechar"' in labelled


@pytest.mark.django_db
class TestHome:
    def test_empty_state(self, client: Client, user: Any) -> None:
        client.force_login(user)
        html = client.get(reverse("home")).content.decode()
        assert "Boas-vindas ao Moneta" in html  # sem dados: primeiros passos, não gráficos zerados
        assert 'data-chart="columns"' not in html
        assert "em breve" not in html  # todas as páginas do menu já existem
        assert f'href="{reverse("portability:index")}"' in html

    def test_shows_balances_and_signed_amounts(self, client: Client, user: Any) -> None:
        account = ensure_default_account(user)
        account.opening_date = datetime.date(2026, 1, 1)
        account.save()
        categories = Category.objects.for_user(user)
        for kind, cents, category in [
            (TransactionKind.INCOME, 500_000, categories.get(name="Salário")),
            (TransactionKind.EXPENSE, 4_590, categories.get(name="Alimentação")),
        ]:
            create_transaction(
                user,
                TransactionData(
                    kind=kind,
                    amount_cents=cents,
                    date=datetime.date(2026, 10, 1),
                    account=account,
                    category=category,
                    tags=["Almoço"] if kind == TransactionKind.EXPENSE else [],
                ),
            )
        client.force_login(user)
        html = client.get(reverse("home")).content.decode()
        assert "R$\xa04.954,10" in html  # saldo
        assert "+R$\xa05.000,00" in html
        assert "-R$\xa045,90" in html
        assert "#Almoço" in html

    def test_inline_script_carries_csp_nonce(self, client: Client, user: Any) -> None:
        client.force_login(user)
        response = client.get(reverse("home"))
        nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"])
        assert nonce
        assert f'nonce="{nonce.group(1)}"' in response.content.decode()

    def test_other_users_data_is_not_shown(
        self, client: Client, user: Any, other_user: Any
    ) -> None:
        theirs = ensure_default_account(other_user)
        theirs.name = "Conta do Bruno"
        theirs.save()
        client.force_login(user)
        assert "Conta do Bruno" not in client.get(reverse("home")).content.decode()


@pytest.mark.django_db
def test_login_page_renders_styled_form(client: Client) -> None:
    html = client.get(reverse("login")).content.decode()
    assert 'autocomplete="current-password"' in html
    assert "css/app.css" in html


def test_category_icons_exist_in_sprite() -> None:
    from apps.taxonomy.defaults import DEFAULT_CATEGORIES
    from apps.taxonomy.icons import CATEGORY_ICONS

    sprite = (Path(settings.BASE_DIR) / "static" / "icons" / "sprite.svg").read_text("utf-8")
    names = {name for name, _ in CATEGORY_ICONS} | {d.icon for d in DEFAULT_CATEGORIES}
    missing = [n for n in names if f'<symbol id="{n}"' not in sprite]
    assert not missing, f"ícones fora do sprite: {missing}"
    assert {d.icon for d in DEFAULT_CATEGORIES} <= {name for name, _ in CATEGORY_ICONS}


@pytest.mark.django_db
def test_custom_404_and_skip_link(client: Client, user: Any) -> None:
    client.force_login(user)
    response = client.get("/nao-existe/")
    assert response.status_code == 404
    assert "Página não encontrada" in response.content.decode()
    home = client.get(reverse("home")).content.decode()
    assert '<a class="skip-link" href="#conteudo">' in home
    assert 'id="conteudo" tabindex="-1"' in home
