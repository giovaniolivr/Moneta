from django.urls import path

from . import views

app_name = "shopping"

urlpatterns = [
    path("desejos/", views.index, name="index"),
    path("desejos/novo/", views.item_create, name="item_create"),
    path("desejos/<uuid:pk>/", views.item_detail, name="item_detail"),
    path("desejos/<uuid:pk>/editar/", views.item_update, name="item_update"),
    path("desejos/<uuid:pk>/resolver/", views.item_toggle_done, name="item_toggle_done"),
    path("desejos/<uuid:pk>/excluir/", views.item_delete, name="item_delete"),
    path("desejos/<uuid:pk>/precos/novo/", views.observation_create, name="observation_create"),
    path(
        "desejos/precos/<uuid:pk>/excluir/",
        views.observation_delete,
        name="observation_delete",
    ),
]
