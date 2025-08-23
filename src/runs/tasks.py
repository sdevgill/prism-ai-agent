from __future__ import annotations

import base64
import json
import logging
from typing import Any, Iterable

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from openai import OpenAI, OpenAIError

from celery import shared_task
from src.assets.models import Asset
from src.runs.models import (
    Prompt,
    PromptKind,
    Run,
    RunStatus,
    Step,
    StepKind,
    StepStatus,
)
from src.runs.tokenization import PROMPT_TOKEN_LIMIT, truncate_to_limit

logger = logging.getLogger(__name__)


def _build_prompt_instruction(modalities: Iterable[str]) -> str:
    """Construct the instruction sent to OpenAI for JSON prompt synthesis."""

    readable = ", ".join(sorted(modalities))
    return (
        "You are a creative director generating downstream prompts for generative models. "
        "Return a JSON object with the keys '<modality>_prompt' for each requested modality. "
        "Each prompt should be detailed and reference the source material faithfully. "
        "Paraphrase rather than quote the original text. Requested modalities: "
        f"{readable}."
    )


@shared_task(bind=True, ignore_result=True)
def generate_prompts_for_run(
    self,
    run_id: str,
    *,
    title: str,
    submitted_url: str | None = None,
    source_text: str | None = None,
    modalities: Iterable[str] | None = None,
    image_options: dict[str, Any] | None = None,
) -> None:
    """Invoke GPT-5 to produce modality prompts and persist them."""

    try:
        run = Run.objects.get(id=run_id)
    except Run.DoesNotExist:
        logger.warning("Run %s disappeared before prompt generation", run_id)
        return

    modalities = list(modalities or [])
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        _mark_run_failed(run, "OpenAI API key is not configured.")
        return

    if not modalities:
        _mark_run_failed(run, "At least one modality must be requested.")
        return

    with transaction.atomic():
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.orchestrator_provider = "openai"
        run.orchestrator_model = settings.OPENAI_RESPONSES_MODEL
        run.save(
            update_fields=[
                "status",
                "started_at",
                "orchestrator_provider",
                "orchestrator_model",
                "updated_at",
            ]
        )

        step, _ = Step.objects.select_for_update().get_or_create(
            run=run,
            kind=StepKind.ANALYZE,
            defaults={"status": StepStatus.PENDING},
        )
        step.status = StepStatus.RUNNING
        step.started_at = step.started_at or timezone.now()
        step.detail = "Calling OpenAI to craft modality prompts."
        step.save(update_fields=["status", "started_at", "detail", "updated_at"])

    client = OpenAI(api_key=api_key)
    instruction = _build_prompt_instruction(modalities)
    Run.objects.filter(id=run.id).update(orchestration_prompt=instruction)
    user_blocks: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "Run title: "
                f"{title}. Focus on the key value proposition, target audience, and desired tone."
            ),
        }
    ]

    if submitted_url:
        user_blocks.append(
            {
                "type": "input_text",
                "text": (
                    "Source URL: "
                    f"{submitted_url}. Use the browsing tool to pull the latest content. "
                    "Skip cached summaries."
                ),
            }
        )
    if source_text:
        truncated = truncate_to_limit(source_text, PROMPT_TOKEN_LIMIT)
        if truncated:
            user_blocks.append(
                {
                    "type": "input_text",
                    "text": f"Direct source text (may be truncated):\n{truncated}",
                }
            )

    request_kwargs: dict[str, Any] = {}
    if submitted_url:
        request_kwargs["tools"] = [{"type": "web_search"}]

    try:
        response = client.responses.create(
            model=settings.OPENAI_RESPONSES_MODEL,
            input=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": user_blocks},
            ],
            reasoning={"effort": "medium", "summary": None},
            text={"format": {"type": "text"}, "verbosity": "medium"},
            **request_kwargs,
        )
    except OpenAIError as exc:
        logger.exception("OpenAI prompt generation failed for run %s", run_id)
        _mark_run_failed(run, f"OpenAI error: {exc}")
        return

    output_text = getattr(response, "output_text", None)
    if not output_text:
        logger.error("OpenAI returned no text output for run %s", run_id)
        _mark_run_failed(run, "OpenAI returned an empty response.")
        return

    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        logger.exception("OpenAI response was not valid JSON for run %s", run_id)
        _mark_run_failed(run, f"Could not parse OpenAI response: {exc}")
        return

    saved_prompts = 0
    with transaction.atomic():
        step = Step.objects.select_for_update().get(run=run, kind=StepKind.ANALYZE)
        for modality in modalities:
            key = f"{modality}_prompt"
            prompt_text = payload.get(key)
            if not prompt_text:
                logger.warning("OpenAI response missing %s for run %s", key, run_id)
                continue
            metadata = {"raw_response": payload}
            if modality == PromptKind.IMAGE and image_options:
                metadata["image_options"] = image_options
            Prompt.objects.update_or_create(
                run=run,
                kind=modality,
                defaults={
                    "content": prompt_text,
                    "metadata": metadata,
                    "step": step,
                },
            )
            saved_prompts += 1

        if saved_prompts == 0:
            step.status = StepStatus.FAILED
            step.detail = "OpenAI response did not include any prompts."
            step.finished_at = timezone.now()
            step.save(update_fields=["status", "detail", "finished_at", "updated_at"])
            run.status = RunStatus.FAILED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
            return

        step.status = StepStatus.COMPLETED
        step.finished_at = timezone.now()
        step.detail = (
            f"Stored OpenAI prompts for downstream generation ({saved_prompts} modalities)."
        )
        step.save(update_fields=["status", "detail", "finished_at", "updated_at"])

    run.refresh_from_db()
    _schedule_downstream_generation(run)
    run.refresh_from_db()
    _maybe_finalize_run(run)


def _mark_run_failed(run: Run, message: str) -> None:
    """Utility to set run and analyze step into a failed state."""

    with transaction.atomic():
        run.status = RunStatus.FAILED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at"])
        step, _ = Step.objects.get_or_create(run=run, kind=StepKind.ANALYZE)
        step.status = StepStatus.FAILED
        step.detail = message
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "detail", "finished_at", "updated_at"])


def _schedule_downstream_generation(run: Run) -> None:
    """Queue follow-up generation tasks based on requested modalities."""

    modalities = set(run.requested_modalities or [])
    if PromptKind.IMAGE in modalities:
        _queue_image_generation(run)


def _queue_image_generation(run: Run) -> None:
    """Create or reset the image step and enqueue the Celery task."""

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

    with transaction.atomic():
        step, _ = Step.objects.select_for_update().get_or_create(
            run=run,
            kind=StepKind.IMAGE,
            defaults={"status": StepStatus.PENDING},
        )
        if step.status == StepStatus.RUNNING:
            return
        if step.status == StepStatus.COMPLETED:
            return

        step.status = StepStatus.PENDING
        step.detail = (
            f"Queued GPT-Image-1 generation ({count} image(s), {quality} quality, {size})."
        )
        step.started_at = None
        step.finished_at = None
        step.save(
            update_fields=[
                "status",
                "detail",
                "started_at",
                "finished_at",
                "updated_at",
            ]
        )

    generate_images_for_run.delay(str(run.id))


def _expected_step_kinds(run: Run) -> set[str]:
    """Return the set of step kinds required for the run to finish."""

    kinds: set[str] = {StepKind.ANALYZE}
    modalities = set(run.requested_modalities or [])
    if PromptKind.IMAGE in modalities:
        kinds.add(StepKind.IMAGE)
    # Additional modalities (audio/video) will be added here when implemented.
    return kinds


def _maybe_finalize_run(run: Run) -> None:
    """Update run status based on downstream step completion."""

    expected_kinds = _expected_step_kinds(run)
    steps = list(Step.objects.filter(run=run, kind__in=expected_kinds))
    if not steps:
        return

    status_map = {step.kind: step.status for step in steps}

    if StepStatus.FAILED in status_map.values():
        Run.objects.filter(id=run.id).update(
            status=RunStatus.FAILED,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return

    if expected_kinds.issubset(status_map.keys()) and all(
        status_map[kind] == StepStatus.COMPLETED for kind in expected_kinds
    ):
        Run.objects.filter(id=run.id).update(
            status=RunStatus.COMPLETED,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
    else:
        Run.objects.filter(id=run.id).update(
            status=RunStatus.RUNNING,
            finished_at=None,
            updated_at=timezone.now(),
        )


def _mark_step_failed(run: Run, step: Step, message: str) -> None:
    """Mark a non-analyze step as failed and propagate run failure."""

    with transaction.atomic():
        step.status = StepStatus.FAILED
        step.detail = message
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "detail", "finished_at", "updated_at"])
        run.status = RunStatus.FAILED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at"])


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
