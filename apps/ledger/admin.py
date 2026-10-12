from django.contrib import admin

from .models import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin[Account]):
    list_display = ["name", "kind", "opening_balance_cents", "opening_date", "is_archived", "owner"]
    list_filter = ["kind", "is_archived", "owner"]
    search_fields = ["name", "normalized_name"]
    readonly_fields = ["normalized_name", "currency", "created_at", "updated_at"]
