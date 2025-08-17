import uuid

from django.conf import settings
from django.db import models


class RunStatus(models.TextChoices):
    """Allowed lifecycle states for an orchestration run."""

    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class StepKind(models.TextChoices):
    """Enumerate key orchestration steps."""

    EXTRACT = "EXTRACT", "Extract"
    ANALYZE = "ANALYZE", "Analyze"
    IMAGE = "IMAGE", "Image"
    AUDIO = "AUDIO", "Audio"
    VIDEO = "VIDEO", "Video"


class StepStatus(models.TextChoices):
    """Track fine-grained progress for individual steps."""

    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class PromptKind(models.TextChoices):
    """Enumerate supported downstream modalities."""

    IMAGE = "image", "Image"
    AUDIO = "audio", "Audio"
    VIDEO = "video", "Video"


class Run(models.Model):
    """A single orchestration request initiated by a user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    title = models.CharField(max_length=160)
    submitted_url = models.URLField(blank=True)
    status = models.CharField(
        max_length=12,
        choices=RunStatus.choices,
        default=RunStatus.PENDING,
    )
    requested_modalities = models.JSONField(default=list, blank=True)
    params = models.JSONField(default=dict, blank=True)
    orchestrator_provider = models.CharField(max_length=100, blank=True)
    orchestrator_model = models.CharField(max_length=100, blank=True)
    orchestration_prompt = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.title} ({self.get_status_display()})"


class Step(models.Model):
    """An atomic unit of work executed as part of a run."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="steps")
    kind = models.CharField(max_length=12, choices=StepKind.choices)
    status = models.CharField(
        max_length=12,
        choices=StepStatus.choices,
        default=StepStatus.PENDING,
    )
    detail = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("run", "kind")
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.run.title} · {self.get_kind_display()}"


class Prompt(models.Model):
    """Stores modality-specific prompts returned by OpenAI."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="prompts")
    step = models.ForeignKey(
        Step,
        on_delete=models.CASCADE,
        related_name="prompts",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=10, choices=PromptKind.choices)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("run", "kind")
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.run.title} · {self.get_kind_display()} prompt"
