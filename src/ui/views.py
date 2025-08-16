from django.views.generic import TemplateView


class HomeView(TemplateView):
    """
    Render the public marketing page that introduces the Prism orchestration flow.
    """

    template_name = "ui/home.html"
