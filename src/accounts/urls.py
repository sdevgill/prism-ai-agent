from django.urls import path

from src.accounts.views import ProfileView

app_name = "accounts"

urlpatterns = [
    path("", ProfileView.as_view(), name="profile"),
]
