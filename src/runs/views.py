from django.views.generic import TemplateView


class RunListView(TemplateView):
    """
    Outline the orchestration hub where pipeline runs and their progress will surface.
    """

    template_name = "runs/list.html"
