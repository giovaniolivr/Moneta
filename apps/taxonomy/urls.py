from django.urls import path

from . import views

app_name = "taxonomy"

urlpatterns = [
    path("categorias/", views.index, name="index"),
    path("categorias/nova/", views.category_create, name="category_create"),
    path("categorias/<uuid:pk>/editar/", views.category_update, name="category_update"),
    path("categorias/<uuid:pk>/arquivar/", views.category_archive, name="category_archive"),
    path("categorias/<uuid:pk>/excluir/", views.category_delete, name="category_delete"),
    path("tags/<uuid:pk>/", views.tag_edit, name="tag_edit"),
    path("tags/<uuid:pk>/apelidos/", views.tag_alias_add, name="tag_alias_add"),
    path("tags/apelidos/<uuid:pk>/remover/", views.tag_alias_remove, name="tag_alias_remove"),
    path("tags/<uuid:pk>/mesclar/", views.tag_merge, name="tag_merge"),
    path("tags/<uuid:pk>/excluir/", views.tag_delete, name="tag_delete"),
]
