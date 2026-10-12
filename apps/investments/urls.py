from django.urls import path

from . import views

app_name = "investments"

urlpatterns = [
    path("investimentos/", views.index, name="index"),
    path("investimentos/simulador/", views.simulator, name="simulator"),
    path("investimentos/compras/", views.purchase, name="purchase"),
    path("investimentos/taxas/atualizar/", views.rates_update, name="rates_update"),
    path("investimentos/taxas/informar/", views.rate_override, name="rate_override"),
    path("investimentos/produtos/novo/", views.product_create, name="product_create"),
    path("investimentos/produtos/<uuid:pk>/", views.product_update, name="product_update"),
    path(
        "investimentos/produtos/<uuid:pk>/excluir/",
        views.product_delete,
        name="product_delete",
    ),
]
