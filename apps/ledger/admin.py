from django.contrib import admin

from apps.core.money import format_brl

from .models import Account, Transaction


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin[Account]):
    list_display = ["name", "kind", "opening_balance_cents", "opening_date", "is_archived", "owner"]
    list_filter = ["kind", "is_archived", "owner"]
    search_fields = ["name", "normalized_name"]
    readonly_fields = ["normalized_name", "currency", "created_at", "updated_at"]


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin[Transaction]):
    list_display = ["date", "kind", "amount", "account", "destination_account", "category", "owner"]
    list_filter = ["kind", "payment_method", "account", "category", "owner"]
    search_fields = ["description", "notes", "tags__name"]
    date_hierarchy = "date"
    autocomplete_fields = ["tags"]
    list_select_related = ["account", "destination_account", "category", "owner"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="valor", ordering="amount_cents")
    def amount(self, obj: Transaction) -> str:
        return format_brl(obj.amount_cents)
