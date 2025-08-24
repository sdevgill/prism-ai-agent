from __future__ import annotations

import base64
import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from openai import OpenAI, OpenAIError

from celery import shared_task
from src.assets.models import Asset
from src.runs.models import Prompt, PromptKind, Run, Step, StepKind, StepStatus

from .common import _mark_step_failed, _maybe_finalize_run

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def generate_images_for_run(self, run_id: str) -> None:
    """Call GPT-Image-1 to create renders for a run."""

    try:
        run = Run.objects.get(id=run_id)
    except Run.DoesNotExist:
        logger.warning("Run %s disappeared before image generation", run_id)
        return

    try:
        step = Step.objects.get(run=run, kind=StepKind.IMAGE)
    except Step.DoesNotExist:
        logger.error("Image step missing for run %s", run_id)
        return

    prompt = Prompt.objects.filter(run=run, kind=PromptKind.IMAGE).order_by("-created_at").first()
    if not prompt:
        _mark_step_failed(run, step, "No stored image prompt to process.")
        return

    options = (run.params or {}).get("image") or {}
    count = options.get("count") or 3
    quality = options.get("quality") or getattr(
        settings,
        "OPENAI_IMAGE_QUALITY",
        "medium",
    )
    size = options.get("size") or getattr(
        settings,
        "OPENAI_IMAGE_SIZE",
        "1024x1536",
    )
    count = max(1, min(int(count), 3))
    if quality not in {"low", "medium", "high"}:
        quality = "medium"
    if size not in {"1024x1024", "1024x1536", "1536x1024"}:
        size = getattr(settings, "OPENAI_IMAGE_SIZE", "1024x1536")

    with transaction.atomic():
        step = Step.objects.select_for_update().get(run=run, kind=StepKind.IMAGE)
        step.status = StepStatus.RUNNING
        step.started_at = step.started_at or timezone.now()
        step.detail = f"Generating {count} GPT-Image-1 render(s) at {quality} quality, {size}."
        step.save(update_fields=["status", "started_at", "detail", "updated_at"])

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        _mark_step_failed(run, step, "OpenAI API key is not configured.")
        return

    client = OpenAI(api_key=api_key)

    try:
        response = client.images.generate(
            model=getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-1"),
            prompt=prompt.content,
            quality=quality,
            size=size,
            n=count,
        )
    except OpenAIError as exc:
        logger.exception("GPT-Image-1 generation failed for run %s", run_id)
        _mark_step_failed(run, step, f"OpenAI error: {exc}")
        return

    data = getattr(response, "data", None) or []
    if not data:
        _mark_step_failed(run, step, "OpenAI returned no image data.")
        return

    Asset.objects.filter(run=run, step=step, kind=PromptKind.IMAGE).delete()

    created_assets = 0
    for idx, item in enumerate(data, start=1):
        b64_content = getattr(item, "b64_json", None)
        if not b64_content:
            logger.warning("Image payload missing b64_json for run %s index %s", run_id, idx)
            continue
        try:
            binary = base64.b64decode(b64_content)
        except (ValueError, TypeError):
            logger.exception("Failed to decode image %s for run %s", idx, run_id)
            continue

        asset = Asset(
            run=run,
            step=step,
            kind=PromptKind.IMAGE,
            title=f"{run.title} - Image {idx}",
            metadata={
                "provider": "openai",
                "model": getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-1"),
                "quality": quality,
                "size": size,
                "index": idx,
                "prompt_id": str(prompt.id),
            },
        )
        asset.file.save(
            f"image_{idx}.png",
            ContentFile(binary),
            save=True,
        )
        created_assets += 1

    if created_assets == 0:
        _mark_step_failed(run, step, "Image decoding failed for all renders.")
        return

    with transaction.atomic():
        step = Step.objects.select_for_update().get(run=run, kind=StepKind.IMAGE)
        step.status = StepStatus.COMPLETED
        step.finished_at = timezone.now()
        step.detail = f"Saved {created_assets} GPT-Image-1 render(s)."
        step.save(update_fields=["status", "finished_at", "detail", "updated_at"])

    run.refresh_from_db()
    _maybe_finalize_run(run)
