from django.urls import path

from . import views

app_name = "goals"

urlpatterns = [
    path("planejamento/metas/nova/", views.goal_create, name="goal_create"),
    path("planejamento/metas/<uuid:pk>/", views.goal_update, name="goal_update"),
    path("planejamento/metas/<uuid:pk>/excluir/", views.goal_delete, name="goal_delete"),
]
