from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import User


class CustomUserCreationForm(UserCreationForm):
    """
    Admin-facing form for creating users with email-only login.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)


class CustomUserChangeForm(UserChangeForm):
    """
    Admin-facing form for updating user details.
    """

    class Meta(UserChangeForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name")
