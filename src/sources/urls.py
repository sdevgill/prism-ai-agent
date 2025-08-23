from django.urls import path

from src.sources.views import RunStatusFragmentView, SourceIngestView

app_name = "sources"

urlpatterns = [
    path("", SourceIngestView.as_view(), name="ingest"),
    path("runs/<uuid:pk>/status/", RunStatusFragmentView.as_view(), name="run-status"),
]
