"""Admin registrations for run orchestration models."""

from django.contrib import admin

from src.runs.models import Prompt, Run, Step


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    """Display basic run metadata in the admin."""

    list_display = (
        "title",
        "owner",
        "status",
        "submitted_url",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("title", "owner__email")
    ordering = ("-created_at",)


@admin.register(Step)
class StepAdmin(admin.ModelAdmin):
    """Show per-step progress for a run."""

    list_display = ("run", "kind", "status", "created_at", "updated_at")
    list_filter = ("kind", "status")
    search_fields = ("run__title",)


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    """Surface generated prompts for inspection."""

    list_display = ("run", "kind", "created_at")
    list_filter = ("kind",)
    search_fields = ("run__title", "content")
