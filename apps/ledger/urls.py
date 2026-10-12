from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("movimentacoes/", views.transaction_list, name="transaction_list"),
    path("movimentacoes/nova/", views.transaction_create, name="transaction_create"),
    path("movimentacoes/lote/", views.transaction_batch, name="transaction_batch"),
    path("movimentacoes/<uuid:pk>/editar/", views.transaction_update, name="transaction_update"),
    path("movimentacoes/<uuid:pk>/excluir/", views.transaction_delete, name="transaction_delete"),
    path("movimentacoes/desfazer/", views.transaction_restore, name="transaction_restore"),
    path("movimentacoes/previstas/", views.planned_list, name="planned_list"),
    path(
        "movimentacoes/<uuid:pk>/confirmar/", views.transaction_confirm, name="transaction_confirm"
    ),
    path("contas/", views.account_list, name="account_list"),
    path("contas/nova/", views.account_create, name="account_create"),
    path("contas/<uuid:pk>/editar/", views.account_update, name="account_update"),
    path("contas/<uuid:pk>/arquivar/", views.account_archive, name="account_archive"),
    path("contas/<uuid:pk>/excluir/", views.account_delete, name="account_delete"),
]
