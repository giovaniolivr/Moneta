from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("movimentacoes/", views.transaction_list, name="transaction_list"),
    path("movimentacoes/nova/", views.transaction_create, name="transaction_create"),
    path("movimentacoes/<uuid:pk>/editar/", views.transaction_update, name="transaction_update"),
    path("movimentacoes/<uuid:pk>/excluir/", views.transaction_delete, name="transaction_delete"),
]
