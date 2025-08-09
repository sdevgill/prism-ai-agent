from django.views.generic import TemplateView


class SourceIngestView(TemplateView):
    """
    Provide a placeholder surface for submitting URLs or pasted text into the pipeline.
    """

    template_name = "sources/ingest.html"
