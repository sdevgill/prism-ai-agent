from django.urls import path

from src.runs.views import RunListView

app_name = "runs"

urlpatterns = [
    path("", RunListView.as_view(), name="list"),
]
