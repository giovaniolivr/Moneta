from django.urls import path

from . import views

app_name = "investments"

urlpatterns = [
    path("investimentos/", views.index, name="index"),
    path("investimentos/taxas/atualizar/", views.rates_update, name="rates_update"),
    path("investimentos/taxas/informar/", views.rate_override, name="rate_override"),
]
