from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from openai import OpenAI, OpenAIError

from celery import shared_task
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

from .audio import generate_audio_for_run
from .common import _mark_run_failed, _maybe_finalize_run
from .image import generate_images_for_run
from .video import generate_video_for_run

logger = logging.getLogger(__name__)


def _build_prompt_instruction(modalities: Iterable[str]) -> str:
    """Construct the instruction sent to OpenAI for JSON prompt synthesis."""

    readable = ", ".join(sorted(modalities))
    audio_clause = (
        " For the audio_prompt, output only the final narration script"
        " the voice should speak."
        " It must be a single block of plain sentences "
        "(no headings, bullet points, role labels, or stage directions)."
        " Omit music/SFX cues and production guidance."
        " Keep it concise enough to be delivered in about 20 seconds"
        " (~55-70 words)."
        if PromptKind.AUDIO in modalities
        else ""
    )
    video_clause = (
        " For the video_prompt, design a concise storyboard for Google Veo."
        " Limit the action to about 8 seconds, keep shot directions tight,"
        " and stick to 16:9 framing suitable for 720p or 1080p output."
        " Do not reference any system instructions."
        if PromptKind.VIDEO in modalities
        else ""
    )
    return (
        "You are a creative director generating downstream prompts for generative models. "
        "Return a JSON object with the keys '<modality>_prompt' for each requested modality. "
        "Each prompt should be detailed and reference the source material faithfully. "
        "Paraphrase rather than quote the original text."
        + audio_clause
        + video_clause
        + " Requested modalities: "
        + f"{readable}."
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
    audio_options: dict[str, Any] | None = None,
    video_options: dict[str, Any] | None = None,
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
            if modality == PromptKind.AUDIO and audio_options:
                metadata["audio_options"] = audio_options
            if modality == PromptKind.VIDEO and video_options:
                metadata["video_options"] = video_options
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


def _schedule_downstream_generation(run: Run) -> None:
    """Queue follow-up generation tasks based on requested modalities."""

    modalities = set(run.requested_modalities or [])
    if PromptKind.IMAGE in modalities:
        _queue_image_generation(run)
    if PromptKind.AUDIO in modalities:
        _queue_audio_generation(run)
    if PromptKind.VIDEO in modalities:
        _queue_video_generation(run)


def _queue_audio_generation(run: Run) -> None:
    """Create or reset the audio step and enqueue the Celery task."""

    options = (run.params or {}).get("audio") or {}
    voice = (options.get("voice") or getattr(settings, "OPENAI_AUDIO_VOICE", "ash")).lower()
    audio_format = (
        options.get("format") or getattr(settings, "OPENAI_AUDIO_FORMAT", "mp3")
    ).lower()
    model_name = getattr(settings, "OPENAI_AUDIO_MODEL", "gpt-4o-mini-tts")

    summary = f"Queued {model_name} narration with {voice.title()} voice ({audio_format.upper()})."

    with transaction.atomic():
        step, _ = Step.objects.select_for_update().get_or_create(
            run=run,
            kind=StepKind.AUDIO,
            defaults={"status": StepStatus.PENDING},
        )
        if step.status == StepStatus.RUNNING:
            return
        if step.status == StepStatus.COMPLETED:
            return

        step.status = StepStatus.PENDING
        step.detail = summary
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

    generate_audio_for_run.delay(str(run.id))


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


def _queue_video_generation(run: Run) -> None:
    """Create or reset the video step and enqueue the Celery task."""

    options = (run.params or {}).get("video") or {}
    model_name = options.get("model") or getattr(
        settings,
        "GOOGLE_VEO_FAST_MODEL",
        "veo-3.0-fast-generate-001",
    )
    resolution = (
        options.get("resolution")
        or getattr(
            settings,
            "GOOGLE_VEO_DEFAULT_RESOLUTION",
            "720p",
        )
    ).lower()

    with transaction.atomic():
        step, _ = Step.objects.select_for_update().get_or_create(
            run=run,
            kind=StepKind.VIDEO,
            defaults={"status": StepStatus.PENDING},
        )
        if step.status == StepStatus.RUNNING:
            return
        if step.status == StepStatus.COMPLETED:
            return

        step.status = StepStatus.PENDING
        step.detail = f"Queued Veo video render via {model_name} ({resolution.upper()})."
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

    generate_video_for_run.delay(str(run.id))
