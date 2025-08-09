from django.views.generic import TemplateView


class ProfileView(TemplateView):
    """
    Display the placeholder account dashboard until authentication flows are wired up.
    """

    template_name = "accounts/profile.html"
