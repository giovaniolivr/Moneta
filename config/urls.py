from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.dashboard import views as dashboard_views

urlpatterns = [
    path("", dashboard_views.index, name="home"),
    path("entrar/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.ledger.urls")),
    path("", include("apps.taxonomy.urls")),
    path("", include("apps.portability.urls")),
    path("", include("apps.planning.urls")),
    path("", include("apps.goals.urls")),
    path("admin/", admin.site.urls),
]
