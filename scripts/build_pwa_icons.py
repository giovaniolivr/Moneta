"""Gera os ícones do app instalável (PWA) em PNG, sem dependências: o "M" da marca sobre o
amarelo. Rode `uv run python scripts/build_pwa_icons.py` após mudar o desenho.

Os ícones são "maskable": o desenho fica dentro da zona segura central (80%), então o celular
pode recortar em círculo ou quadrado arredondado sem cortar a letra.
"""

import struct
import zlib
from pathlib import Path

YELLOW = (0xFA, 0xFF, 0x69)
INK = (0x0A, 0x0A, 0x0A)
# "M" em grade 9x9 com hastes de 2 células (legível em 48px).
GLYPH = [
    "110000011",
    "111000111",
    "111101111",
    "110111011",
    "110010011",
    "110000011",
    "110000011",
    "110000011",
    "110000011",
]
OUT = Path(__file__).resolve().parent.parent / "static" / "icons"


def render(size: int) -> list[bytearray]:
    rows = [bytearray(YELLOW * size) for _ in range(size)]
    n = len(GLYPH)
    cell = size * 0.44 / n  # o "M" ocupa 44% da largura: folgado na zona segura
    left = top = (size - cell * n) / 2
    for gy, line in enumerate(GLYPH):
        for gx, bit in enumerate(line):
            if bit != "1":
                continue
            x0, y0 = round(left + gx * cell), round(top + gy * cell)
            x1, y1 = round(left + (gx + 1) * cell), round(top + (gy + 1) * cell)
            for y in range(y0, y1):
                for x in range(x0, x1):
                    rows[y][3 * x : 3 * x + 3] = bytes(INK)
    return rows


def png(rows: list[bytearray]) -> bytes:
    height, width = len(rows), len(rows[0]) // 3

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # RGB 8 bits
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in (("pwa-192.png", 192), ("pwa-512.png", 512), ("apple-touch-icon.png", 180)):
        (OUT / name).write_bytes(png(render(size)))
        print(f"static/icons/{name} ({size}px)")


if __name__ == "__main__":
    main()
