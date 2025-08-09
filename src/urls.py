"""
URL configuration for the Prism AI Agent project.

Routes admin, UI landing pages, and app-level URL configurations.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("src.ui.urls")),
    path("accounts/", include("src.accounts.urls")),
    path("sources/", include("src.sources.urls")),
    path("runs/", include("src.runs.urls")),
    path("assets/", include("src.assets.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
