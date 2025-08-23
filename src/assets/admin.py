"""Admin registrations for generated assets."""

from django.contrib import admin

from src.assets.models import Asset


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    """Surface generated assets with helpful filters."""

    list_display = ("title", "kind", "run", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("title", "run__title", "run__owner__email")
    ordering = ("-created_at",)
