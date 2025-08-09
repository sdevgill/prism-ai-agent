from django.views.generic import TemplateView


class HomeView(TemplateView):
    """
    Render the dashboard landing page introducing the Prism orchestration flow.
    """

    template_name = "ui/home.html"
