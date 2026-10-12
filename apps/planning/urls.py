from django.urls import path

from . import views

app_name = "planning"

urlpatterns = [
    path("planejamento/", views.index, name="index"),
    path("planejamento/recorrencias/nova/", views.recurrence_create, name="recurrence_create"),
    path(
        "planejamento/recorrencias/<uuid:pk>/editar/",
        views.recurrence_update,
        name="recurrence_update",
    ),
    path(
        "planejamento/recorrencias/<uuid:pk>/pausar/",
        views.recurrence_toggle,
        name="recurrence_toggle",
    ),
    path(
        "planejamento/recorrencias/<uuid:pk>/excluir/",
        views.recurrence_delete,
        name="recurrence_delete",
    ),
]
