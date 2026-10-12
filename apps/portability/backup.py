"""Backup do SQLite com a API de backup online (cópia consistente mesmo com o app rodando)."""

import datetime
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size_bytes: int
    removed: list[Path]


def _copies(dest_dir: Path, stem: str) -> list[Path]:
    """Cópias deste banco, da mais antiga para a mais nova (o nome tem data e hora)."""
    return sorted(dest_dir.glob(f"{stem}-????????-??????.sqlite3"))


def backup_sqlite(
    source: Path, dest_dir: Path, *, keep: int, now: datetime.datetime
) -> BackupResult:
    """Copia `source` para `dest_dir/<nome>-AAAAMMDD-HHMMSS.sqlite3`, confere a integridade da
    cópia e mantém só as `keep` mais recentes deste banco (bancos diferentes não se misturam)."""
    if keep < 1:
        raise BackupError("É preciso manter pelo menos 1 cópia.")
    if not source.is_file():
        raise BackupError(f"Banco não encontrado: {source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{source.stem}-{now:%Y%m%d-%H%M%S}.sqlite3"
    if target.exists():
        raise BackupError(f"Já existe um backup com este horário: {target.name}")

    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        status = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if status != "ok":
        target.unlink(missing_ok=True)
        raise BackupError(f"A cópia falhou na verificação de integridade: {status}")

    removed = _copies(dest_dir, source.stem)[:-keep]
    for old in removed:
        old.unlink()
    return BackupResult(target, target.stat().st_size, removed)


@dataclass(frozen=True)
class RestoreResult:
    safety_copy: Path  # cópia do banco como estava antes de restaurar


def check_integrity(path: Path) -> str:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        status: str = con.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return str(exc)
    finally:
        con.close()
    return status


def restore_sqlite(
    backup: Path, target: Path, *, safety_dir: Path, now: datetime.datetime
) -> RestoreResult:
    """Substitui `target` pelo conteúdo de `backup`.

    Antes: confere a integridade do backup e guarda uma cópia do banco atual em `safety_dir`
    (nada se perde se o backup escolhido for o errado). Usa a API de backup do SQLite para a
    cópia, então o arquivo de destino nunca fica pela metade.
    """
    if not backup.is_file():
        raise BackupError(f"Backup não encontrado: {backup}")
    if backup.resolve() == target.resolve():
        raise BackupError("O backup e o banco em uso são o mesmo arquivo.")
    status = check_integrity(backup)
    if status != "ok":
        raise BackupError(f"O backup está corrompido: {status}")
    safety = backup_sqlite(target, safety_dir, keep=1_000, now=now).path
    src = sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if check_integrity(target) != "ok":
        raise BackupError(f"A restauração falhou; o banco anterior está salvo em {safety}")
    return RestoreResult(safety)
