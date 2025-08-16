from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class SourceIngestView(LoginRequiredMixin, TemplateView):
    """
    Provide a placeholder surface for submitting URLs or pasted text into the pipeline.
    """

    template_name = "sources/ingest.html"
