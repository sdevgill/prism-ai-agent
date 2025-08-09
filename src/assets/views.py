from django.views.generic import TemplateView


class AssetLibraryView(TemplateView):
    """
    Provide a placeholder hub for browsing generated media assets across modalities.
    """

    template_name = "assets/library.html"
