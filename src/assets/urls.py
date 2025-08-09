from django.urls import path

from src.assets.views import AssetLibraryView

app_name = "assets"

urlpatterns = [
    path("", AssetLibraryView.as_view(), name="library"),
]
