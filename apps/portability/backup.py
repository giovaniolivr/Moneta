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
