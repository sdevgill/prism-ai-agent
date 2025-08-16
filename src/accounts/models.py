from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from src.accounts.managers import UserManager


class User(AbstractUser):
    """Custom user model that uses email as the unique identifier."""

    username = None
    email = models.EmailField(_("email address"), unique=True)
    company_name = models.CharField(
        _("company name"),
        max_length=255,
        blank=True,
        help_text=_("Optional organization name associated with the account."),
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self) -> str:
        return self.email
