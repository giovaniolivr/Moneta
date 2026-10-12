"""Restauração de backup: confere integridade, guarda o banco atual antes, nunca pela metade."""

import datetime
import sqlite3
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from apps.portability.backup import BackupError, backup_sqlite, check_integrity, restore_sqlite

NOW = datetime.datetime(2026, 10, 12, 9, 0, 0)


def make_db(path: Path, value: int) -> None:
    con = sqlite3.connect(path)
    con.execute("create table t (x)")
    con.execute("insert into t values (?)", (value,))
    con.commit()
    con.close()


def read(path: Path) -> int:
    con = sqlite3.connect(path)
    value: int = con.execute("select x from t").fetchone()[0]
    con.close()
    return value


def test_restore_keeps_a_safety_copy(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_db(db, 1)
    old = backup_sqlite(db, tmp_path / "backups", keep=5, now=NOW).path
    con = sqlite3.connect(db)
    con.execute("update t set x = 2")
    con.commit()
    con.close()

    result = restore_sqlite(
        old, db, safety_dir=tmp_path / "backups", now=NOW + datetime.timedelta(minutes=1)
    )
    assert read(db) == 1  # voltou ao backup
    assert read(result.safety_copy) == 2  # o estado de antes não se perdeu
    assert check_integrity(db) == "ok"


def test_refuses_corrupt_or_missing_or_same_file(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_db(db, 1)
    corrupt = tmp_path / "db-20261012-080000.sqlite3"
    corrupt.write_bytes(b"isto nao e um banco sqlite" * 100)
    with pytest.raises(BackupError, match="corrompido"):
        restore_sqlite(corrupt, db, safety_dir=tmp_path, now=NOW)
    assert read(db) == 1
    with pytest.raises(BackupError, match="não encontrado"):
        restore_sqlite(tmp_path / "nada.sqlite3", db, safety_dir=tmp_path, now=NOW)
    with pytest.raises(BackupError, match="mesmo arquivo"):
        restore_sqlite(db, db, safety_dir=tmp_path, now=NOW)


@pytest.mark.django_db
def test_command_guards() -> None:
    # O banco de teste é em memória: o comando recusa antes de tocar em qualquer coisa.
    with pytest.raises(CommandError, match="memória"):
        call_command("restaurar_backup", "backups/db-20261012-080000.sqlite3")
