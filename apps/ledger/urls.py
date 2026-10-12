from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("movimentacoes/nova/", views.transaction_create, name="transaction_create"),
]
