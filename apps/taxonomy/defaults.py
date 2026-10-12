"""Categorias criadas para todo usuário novo (editáveis e arquiváveis depois).

"Investimentos" não é categoria de despesa: guardar dinheiro é transferência para uma conta de
investimento — não é consumo e não deve inflar as despesas.
"""

from typing import NamedTuple


class DefaultCategory(NamedTuple):
    name: str
    kind: str
    color: str
    icon: str


DEFAULT_CATEGORIES: tuple[DefaultCategory, ...] = (
    DefaultCategory("Alimentação", "expense", "1", "utensils"),
    DefaultCategory("Transporte", "expense", "2", "car"),
    DefaultCategory("Moradia", "expense", "3", "house"),
    DefaultCategory("Saúde", "expense", "4", "heart-pulse"),
    DefaultCategory("Educação", "expense", "5", "graduation-cap"),
    DefaultCategory("Lazer", "expense", "6", "gamepad-2"),
    DefaultCategory("Compras", "expense", "7", "shopping-bag"),
    DefaultCategory("Assinaturas", "expense", "8", "repeat"),
    DefaultCategory("Outros", "expense", "1", "circle-ellipsis"),
    DefaultCategory("Salário", "income", "2", "wallet"),
    DefaultCategory("Rendimentos", "income", "3", "trending-up"),
    DefaultCategory("Outras receitas", "income", "4", "plus"),
)
