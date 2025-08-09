from django.urls import path

from src.sources.views import SourceIngestView

app_name = "sources"

urlpatterns = [
    path("", SourceIngestView.as_view(), name="ingest"),
]
