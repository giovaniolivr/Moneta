"""Telas de dados (download, prévia → confirmação), CSV e comando de backup."""

import csv
import datetime
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ledger.models import Transaction
from apps.ledger.services import TransactionData, create_transaction, ensure_default_account
from apps.portability.backup import BackupError, backup_sqlite
from apps.portability.selectors import export_payload
from apps.taxonomy.models import Category

D = datetime.date
TODAY = D(2026, 10, 10)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)


@pytest.fixture
def logged(client: Client, user: Any) -> Client:
    account = ensure_default_account(user)
    cats = {c.name: c for c in Category.objects.for_user(user)}
    for cents, day, name, description in [
        (500_000, D(2026, 10, 5), "Salário", "Salário"),
        (123_456, D(2026, 10, 2), "Alimentação", '=HYPERLINK("x")'),
        (3_000, D(2026, 9, 9), "Transporte", "Uber"),
    ]:
        create_transaction(
            user,
            TransactionData(
                kind="income" if name == "Salário" else "expense",
                amount_cents=cents,
                date=day,
                account=account,
                category=cats[name],
                tags=["Mercado"] if name == "Alimentação" else [],
                description=description,
            ),
        )
    client.force_login(user)
    return client


def _csv(response: Any) -> list[list[str]]:
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")  # BOM: o Excel abre os acentos corretamente
    return list(csv.reader(io.StringIO(text[1:]), delimiter=";"))


@pytest.mark.django_db
class TestViews:
    def test_index(self, logged: Client) -> None:
        html = logged.get(reverse("portability:index")).content.decode()
        assert "Exportar tudo (JSON)" in html
        assert "manage.py backup" in html
        assert 'enctype="multipart/form-data"' in html

    def test_export_json_download(self, logged: Client, user: Any) -> None:
        response = logged.get(reverse("portability:export_json"))
        assert response["Content-Disposition"] == 'attachment; filename="moneta-2026-10-10.json"'
        payload = json.loads(response.content)
        assert payload["format"] == "moneta"
        assert len(payload["transactions"]) == 3

    def test_csv_uses_list_filters_and_neutralizes_formulas(self, logged: Client) -> None:
        rows = _csv(
            logged.get(reverse("portability:transactions_csv") + "?periodo=mes&tipo=expense")
        )
        header, *body = rows
        assert header[:3] == ["Data", "Tipo", "Valor"]
        assert body == [
            [
                "02/10/2026",
                "Despesa",
                "1234,56",
                "Principal",
                "",
                "Alimentação",
                "Mercado",
                "",
                '\'=HYPERLINK("x")',
                "",
            ]
        ]
        everything = _csv(logged.get(reverse("portability:transactions_csv") + "?periodo=tudo"))
        assert len(everything) == 4

    def test_csv_is_scoped(self, logged: Client, other_user: Any, client: Client) -> None:
        client.force_login(other_user)
        assert len(_csv(client.get(reverse("portability:transactions_csv") + "?periodo=tudo"))) == 1

    def test_preview_then_confirm(self, logged: Client, user: Any) -> None:
        payload = export_payload(user)
        Transaction.objects.for_user(user).delete()
        upload = SimpleUploadedFile("backup.json", json.dumps(payload).encode())
        html = logged.post(reverse("portability:import_preview"), {"file": upload}).content.decode()
        assert "Prévia da importação" in html
        assert "<td>3</td>" in html  # 3 movimentações novas
        assert Transaction.objects.for_user(user).count() == 0  # prévia não grava

        response = logged.post(reverse("portability:import_confirm"))
        assert response.status_code == 302
        assert Transaction.objects.for_user(user).count() == 3
        # A confirmação consome o arquivo da sessão: repetir não reimporta.
        follow = logged.post(reverse("portability:import_confirm"), follow=True)
        assert "Nenhuma importação pendente" in follow.content.decode()

    def test_preview_lists_errors_and_writes_nothing(self, logged: Client, user: Any) -> None:
        payload = export_payload(user)
        payload["transactions"][0]["amount_cents"] = -5
        Transaction.objects.for_user(user).delete()
        upload = SimpleUploadedFile("ruim.json", json.dumps(payload).encode())
        html = logged.post(reverse("portability:import_preview"), {"file": upload}).content.decode()
        assert "Nada foi importado" in html
        assert "Movimentação 1" in html
        assert Transaction.objects.for_user(user).count() == 0

    def test_invalid_file(self, logged: Client) -> None:
        upload = SimpleUploadedFile("x.json", b"not json")
        html = logged.post(reverse("portability:import_preview"), {"file": upload}).content.decode()
        assert "não é um JSON válido" in html

    def test_cancel(self, logged: Client, user: Any) -> None:
        upload = SimpleUploadedFile("b.json", json.dumps(export_payload(user)).encode())
        logged.post(reverse("portability:import_preview"), {"file": upload})
        logged.post(reverse("portability:import_cancel"))
        follow = logged.post(reverse("portability:import_confirm"), follow=True)
        assert "Nenhuma importação pendente" in follow.content.decode()

    def test_export_link_on_transaction_list_keeps_filters(self, logged: Client) -> None:
        html = logged.get(reverse("ledger:transaction_list") + "?periodo=mes&tipo=expense")
        assert f"{reverse('portability:transactions_csv')}?periodo=mes&amp;tipo=expense" in (
            html.content.decode()
        )


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("create table t (x)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()


class TestBackup:
    NOW = datetime.datetime(2026, 10, 12, 8, 30, 0)

    def test_copy_is_consistent(self, tmp_path: Path) -> None:
        source = tmp_path / "db.sqlite3"
        _make_db(source)
        result = backup_sqlite(source, tmp_path / "backups", keep=3, now=self.NOW)
        assert result.path.name == "db-20261012-083000.sqlite3"
        con = sqlite3.connect(result.path)
        assert con.execute("select x from t").fetchall() == [(42,)]
        con.close()

    def test_rotation_keeps_newest_of_the_same_database(self, tmp_path: Path) -> None:
        source, other = tmp_path / "db.sqlite3", tmp_path / "db-demo.sqlite3"
        _make_db(source)
        _make_db(other)
        dest = tmp_path / "backups"
        backup_sqlite(other, dest, keep=1, now=self.NOW)
        for minute in range(4):
            result = backup_sqlite(
                source, dest, keep=2, now=self.NOW + datetime.timedelta(minutes=minute)
            )
        assert [p.name for p in result.removed] == ["db-20261012-083100.sqlite3"]
        assert sorted(p.name for p in dest.iterdir()) == [
            "db-20261012-083200.sqlite3",
            "db-20261012-083300.sqlite3",
            "db-demo-20261012-083000.sqlite3",  # outro banco: rotação separada
        ]

    def test_errors(self, tmp_path: Path) -> None:
        with pytest.raises(BackupError, match="não encontrado"):
            backup_sqlite(tmp_path / "nada.sqlite3", tmp_path, keep=1, now=self.NOW)
        with pytest.raises(BackupError, match="pelo menos 1"):
            backup_sqlite(tmp_path / "nada.sqlite3", tmp_path, keep=0, now=self.NOW)

    @pytest.mark.django_db
    def test_command_refuses_in_memory_database(self) -> None:
        with pytest.raises(CommandError, match="memória"):
            call_command("backup")
