from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from src.accounts.forms import (
    AccountDetailsForm,
    AccountPasswordForm,
    SignInForm,
    SignupForm,
)


class SignupView(FormView):
    """
    Create a new user account and log the user in immediately.
    """

    template_name = "accounts/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("assets:library")

    def form_valid(self, form):
        """
        Persist the new user and establish an authenticated session.
        """

        user = form.save()
        login(self.request, user)
        display_name = user.first_name or user.email
        messages.success(
            self.request,
            f"Welcome to Prism, {display_name}! Your account is ready to go.",
        )
        return super().form_valid(form)


class SignInView(LoginView):
    """
    Authenticate users with email and password credentials.
    """

    template_name = "accounts/login.html"
    authentication_form = SignInForm

    def get_success_url(self):
        """
        Redirect to the dashboard or the provided `next` target.
        """

        return self.get_redirect_url() or str(reverse_lazy("assets:library"))

    def form_valid(self, form):
        """
        Confirm login success before redirecting to the destination.
        """

        user = form.get_user()
        display_name = user.first_name or user.email
        messages.success(
            self.request,
            f"Welcome back, {display_name}! Let's build another run.",
        )
        return super().form_valid(form)


class SignOutView(LogoutView):
    """
    Terminate the current session and redirect to the login screen.
    """

    next_page = reverse_lazy("accounts:login")
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        """
        Log the user out on POST and confirm the action via messages.
        """

        display_name = request.user.first_name or request.user.email
        messages.success(request, f"See you soon, {display_name}.")
        return super().dispatch(request, *args, **kwargs)


class ProfileView(LoginRequiredMixin, TemplateView):
    """
    Display a logged-in user's account data with update and password forms.
    """

    template_name = "accounts/profile.html"

    def get_context_data(self, **kwargs):
        """
        Supply pre-populated account and password forms for the template.
        """

        context = super().get_context_data(**kwargs)
        account_form = kwargs.get("account_form") or AccountDetailsForm(instance=self.request.user)
        password_form = kwargs.get("password_form") or AccountPasswordForm(user=self.request.user)
        context.update(
            {
                "account_form": account_form,
                "password_form": password_form,
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        """
        Handle updates for profile details or password changes.
        """

        action = request.POST.get("form")
        if action == "details":
            account_form = AccountDetailsForm(request.POST, instance=request.user)
            password_form = AccountPasswordForm(user=request.user)
            if account_form.is_valid():
                account_form.save()
                messages.success(request, "Your profile details were saved.")
                return redirect("accounts:profile")
        elif action == "password":
            account_form = AccountDetailsForm(instance=request.user)
            password_form = AccountPasswordForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, "Your password was updated.")
                return redirect("accounts:profile")
        else:
            account_form = AccountDetailsForm(instance=request.user)
            password_form = AccountPasswordForm(user=request.user)
            messages.error(request, "We could not determine which form you submitted.")

        return self.render_to_response(
            self.get_context_data(
                account_form=account_form,
                password_form=password_form,
            )
        )
