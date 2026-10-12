from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def icon(name: str, css_class: str = "", label: str = "") -> str:
    """{% icon "wallet" "icon-sm" %} — ícone do sprite Lucide (static/icons/sprite.svg).

    Sem `label`, o ícone é decorativo (aria-hidden); com `label`, vira imagem acessível.
    """
    href = f"{static('icons/sprite.svg')}#{name}"
    classes = f"icon {css_class}".strip()
    if label:
        return format_html(
            '<svg class="{}" role="img" aria-label="{}"><use href="{}"></use></svg>',
            classes,
            label,
            href,
        )
    return format_html(
        '<svg class="{}" aria-hidden="true" focusable="false"><use href="{}"></use></svg>',
        classes,
        href,
    )
