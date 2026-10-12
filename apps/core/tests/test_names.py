import pytest

from apps.core.names import InvalidNameError, clean_display_name, normalize_name


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("mercado", "mercado"),
        ("Mercado", "mercado"),
        ("MERCADO", "mercado"),
        ("  Mercado  ", "mercado"),
        ("Saúde", "saude"),
        ("SAÚDE", "saude"),
        ("Educação", "educacao"),
        ("Pão  de   Açúcar", "pao de acucar"),
        ("Straße", "strasse"),  # casefold, não lower
        ("ﬁt", "fit"),  # ligadura normalizada (NFKC)
    ],
)
def test_normalize_name(text: str, key: str) -> None:
    assert normalize_name(text) == key


def test_display_name_keeps_case_and_accents() -> None:
    assert clean_display_name("  Saúde   e  Bem-estar ") == "Saúde e Bem-estar"


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_empty_names_are_rejected(text: str) -> None:
    with pytest.raises(InvalidNameError):
        normalize_name(text)
