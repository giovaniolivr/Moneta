from django.urls import path

from . import views

app_name = "portability"

urlpatterns = [
    path("dados/", views.index, name="index"),
    path("dados/exportar.json", views.export_json, name="export_json"),
    path("dados/movimentacoes.csv", views.transactions_csv, name="transactions_csv"),
    path("dados/importar/", views.import_preview, name="import_preview"),
    path("dados/importar/confirmar/", views.import_confirm, name="import_confirm"),
    path("dados/importar/cancelar/", views.import_cancel, name="import_cancel"),
]
