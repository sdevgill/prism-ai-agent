"""Database models for generated downstream assets."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Optional

from django.db import models

from src.runs.models import PromptKind, Run, Step


def asset_upload_path(instance: "Asset", filename: str) -> str:
    """Place asset files under a deterministic run/kind folder."""

    extension = Path(filename).suffix or ".bin"
    return f"assets/{instance.run_id}/{instance.kind}/{instance.id}{extension}"


class Asset(models.Model):
    """Represents a generated artifact (image, audio, or video)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="assets")
    step = models.ForeignKey(Step, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=10, choices=PromptKind.choices)
    file = models.FileField(upload_to=asset_upload_path)
    title = models.CharField(max_length=160, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.run.title} - {self.get_kind_display()} asset"

    @property
    def source_label(self) -> str:
        """Human-readable source for grouping in the UI."""

        return self.run.title or self.run.submitted_url or str(self.run_id)

    @property
    def download_url(self) -> str:
        """Direct link to download the stored file."""

        return self.file.url if self.file else ""

    @property
    def view_url(self) -> str:
        """Alias download URL until a detail view exists."""

        return self.download_url

    @property
    def thumbnail_url(self) -> str:
        """Return the preview URL for image assets."""

        if self.kind == PromptKind.IMAGE and self.file:
            return self.file.url
        return ""

    @property
    def source(self) -> Optional[Run]:  # Template helper compatibility
        """Expose the run for templates expecting `asset.source`."""

        return self.run

    @property
    def filename(self) -> str:
        """Return the stored filename for download prompts."""

        if not self.file:
            return "asset"
        return Path(self.file.name).name

    @property
    def display_metadata(self) -> Dict[str, str]:
        """Return the subset of metadata worth surfacing in the UI."""

        meta = self.metadata or {}
        mappings = {
            "provider": lambda v: "Open AI" if str(v).lower() == "openai" else str(v),
            "model": str,
            "quality": lambda v: str(v).capitalize(),
            "size": str,
        }
        cleaned: Dict[str, str] = {}
        for key, transform in mappings.items():
            value = meta.get(key)
            if value:
                cleaned[key] = transform(value)
        return cleaned

    @property
    def display_title(self) -> str:
        """Provide a normalized title for templates."""

        title = self.title or ""
        return title.replace(" · ", " - ")
