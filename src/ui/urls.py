from django.urls import path

from src.ui.views import HomeView

app_name = "ui"

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
]
