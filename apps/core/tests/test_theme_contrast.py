"""Garante contraste WCAG AA dos tokens nos dois temas: nada de texto invisível ao trocar o tema.

Lê os tokens direto de static/css/app.css — a fonte da verdade usada pelo navegador.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

CSS = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")

TEXT_AA = 4.5  # texto normal
GRAPHIC_AA = 3.0  # elementos gráficos (séries de gráficos, ícones)

BACKGROUNDS = ["canvas", "surface", "surface-2"]
TEXT_TOKENS = ["ink", "body", "muted", "accent-text", "income", "expense", "info", "warning"]
CHART_TOKENS = [f"chart-{i}" for i in range(1, 9)]


def _theme_tokens(selector: str) -> dict[str, str]:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", CSS, re.DOTALL)
    assert match, f"bloco {selector!r} não encontrado em app.css"
    return dict(re.findall(r"--color-([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", match.group(1)))


THEMES = {
    "dark": _theme_tokens(':root[data-theme="dark"]'),
    "light": _theme_tokens(':root[data-theme="light"]'),
}


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_formula() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21)
    assert contrast("#777777", "#777777") == pytest.approx(1)


def test_both_themes_define_the_same_tokens() -> None:
    assert THEMES["dark"].keys() == THEMES["light"].keys()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("fg", TEXT_TOKENS)
@pytest.mark.parametrize("bg", BACKGROUNDS)
def test_text_contrast(theme: str, fg: str, bg: str) -> None:
    tokens = THEMES[theme]
    ratio = contrast(tokens[fg], tokens[bg])
    assert ratio >= TEXT_AA, f"{theme}: {fg} sobre {bg} = {ratio:.2f}"


@pytest.mark.parametrize("theme", THEMES)
def test_primary_button_contrast(theme: str) -> None:
    tokens = THEMES[theme]
    assert contrast(tokens["on-primary"], tokens["primary"]) >= TEXT_AA
    assert contrast(tokens["on-primary"], tokens["primary-active"]) >= TEXT_AA
    # O próprio botão precisa se destacar do fundo da página.
    assert contrast(tokens["primary"], tokens["canvas"]) >= GRAPHIC_AA


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("chart", CHART_TOKENS)
@pytest.mark.parametrize("bg", ["surface", "surface-2"])
def test_chart_colors_are_visible(theme: str, chart: str, bg: str) -> None:
    tokens = THEMES[theme]
    ratio = contrast(tokens[chart], tokens[bg])
    assert ratio >= GRAPHIC_AA, f"{theme}: {chart} sobre {bg} = {ratio:.2f}"


@pytest.mark.parametrize("theme", THEMES)
def test_focus_ring_is_visible(theme: str) -> None:
    tokens = THEMES[theme]
    for bg in BACKGROUNDS:
        assert contrast(tokens["focus"], tokens[bg]) >= GRAPHIC_AA
