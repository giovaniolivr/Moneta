from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", core_views.home, name="home"),
    path("entrar/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.ledger.urls")),
    path("", include("apps.taxonomy.urls")),
    path("admin/", admin.site.urls),
]
