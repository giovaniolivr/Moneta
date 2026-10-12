from django.contrib import admin

from .models import Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin[Category]):
    list_display = ["name", "kind", "color", "icon", "is_archived", "owner"]
    list_filter = ["kind", "is_archived", "owner"]
    search_fields = ["name", "normalized_name"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]
