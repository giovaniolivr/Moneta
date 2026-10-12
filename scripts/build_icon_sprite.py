"""Gera static/icons/sprite.svg a partir dos ícones Lucide listados em NAMES.

Uso: uv run python scripts/build_icon_sprite.py
Para adicionar um ícone: inclua o nome (https://lucide.dev/icons) em NAMES e rode o script.
"""

import pathlib
import re
import urllib.request

VERSION = "1.55.0"
NAMES = [
    "utensils",
    "car",
    "house",
    "heart-pulse",
    "graduation-cap",
    "gamepad-2",
    "shopping-bag",
    "repeat",
    "circle-ellipsis",
    "wallet",
    "trending-up",
    "plus",
    "layout-dashboard",
    "arrow-left-right",
    "tags",
    "landmark",
    "database",
    "sun",
    "moon",
    "log-out",
    "menu",
    "x",
    "chevron-right",
    "arrow-down-left",
    "arrow-up-right",
    "credit-card",
    "piggy-bank",
    "banknote",
    "inbox",
    "ellipsis",
    "circle-alert",
    "circle-check",
    "info",
    "panel-left-close",
    "panel-left-open",
    "pencil",
    "trash-2",
    "funnel",
    "search",
    "calendar",
    "chevron-left",
    "plane",
    "paw-print",
    "baby",
    "shirt",
    "dumbbell",
    "gift",
    "coffee",
    "fuel",
    "bus",
    "smartphone",
    "zap",
    "droplet",
    "wifi",
    "book-open",
    "briefcase",
    "film",
    "music",
    "scissors",
    "wrench",
    "pill",
    "stethoscope",
    "receipt",
    "hand-coins",
    "sparkles",
    "palette",
    "beer",
    "pizza",
    "bike",
    "train-front",
    "tv",
    "laptop",
    "archive",
    "archive-restore",
    "merge",
]

URL = "https://cdn.jsdelivr.net/npm/lucide-static@{version}/icons/{name}.svg"
OUT = pathlib.Path(__file__).resolve().parent.parent / "static" / "icons" / "sprite.svg"
HEADER = (
    f"<!-- Lucide v{VERSION} (ISC License, https://lucide.dev/license). "
    "Gerado por scripts/build_icon_sprite.py; não editar à mão. -->"
)


def symbol(name: str) -> str:
    url = URL.format(version=VERSION, name=name)
    with urllib.request.urlopen(url) as response:  # noqa: S310 — URL fixa do CDN
        svg = response.read().decode("utf-8")
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.DOTALL)
    match = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.DOTALL)
    if match is None:
        raise ValueError(f"SVG inesperado para {name!r}")
    body = "".join(line.strip() for line in match.group(1).splitlines())
    return f'<symbol id="{name}" viewBox="0 0 24 24">{body}</symbol>'


def main() -> None:
    symbols = [symbol(name) for name in NAMES]
    OUT.write_text(
        "\n".join([HEADER, '<svg xmlns="http://www.w3.org/2000/svg">', *symbols, "</svg>", ""]),
        encoding="utf-8",
    )
    print(f"{len(symbols)} icones gravados em {OUT}")


if __name__ == "__main__":
    main()
