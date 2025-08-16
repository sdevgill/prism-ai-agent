from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)

from src.accounts.models import User


class CustomUserCreationForm(UserCreationForm):
    """
    Admin-facing form for creating users with email-only login.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name", "company_name")


class CustomUserChangeForm(UserChangeForm):
    """
    Admin-facing form for updating user details.
    """

    class Meta(UserChangeForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name", "company_name")


class SignupForm(UserCreationForm):
    """
    Public-facing form that collects email and password for new accounts.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name", "company_name")

    field_order = ("email", "first_name", "last_name", "company_name", "password1", "password2")


class SignInForm(AuthenticationForm):
    """
    Authentication form that treats the username as an email.
    """

    username = forms.EmailField(label="Email")


class AccountDetailsForm(forms.ModelForm):
    """
    Allow logged-in users to update account profile details.
    """

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "company_name")


class AccountPasswordForm(PasswordChangeForm):
    """
    Provide a password change form for the account settings page.
    """
