from django.contrib import admin
from django.http import HttpRequest

from .models import Category, Tag, TagAlias


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin[Category]):
    list_display = ["name", "kind", "color", "icon", "is_archived", "owner"]
    list_filter = ["kind", "is_archived", "owner"]
    search_fields = ["name", "normalized_name"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]


class TagAliasInline(admin.TabularInline[TagAlias, Tag]):
    """Somente leitura: apelidos nascem de mesclagens e das telas do app (via services)."""

    model = TagAlias
    fields = ["normalized_alias"]
    readonly_fields = ["normalized_alias"]
    extra = 0
    can_delete = True

    def has_add_permission(self, request: HttpRequest, obj: Tag | None = None) -> bool:
        return False


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin[Tag]):
    list_display = ["name", "normalized_name", "owner"]
    list_filter = ["owner"]
    search_fields = ["name", "normalized_name", "aliases__normalized_alias"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]
    inlines = [TagAliasInline]
