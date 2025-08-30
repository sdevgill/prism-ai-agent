from __future__ import annotations

import base64
import logging
import os
import tempfile
import time
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from google import genai
from google.genai import types

from celery import shared_task
from src.assets.models import Asset
from src.runs.models import Prompt, PromptKind, Run, Step, StepKind, StepStatus

from .common import _mark_step_failed, _maybe_finalize_run

logger = logging.getLogger(__name__)


def _save_video_asset(
    run: Run,
    step: Step,
    prompt: Prompt,
    *,
    payload: bytes,
    provider: str,
    model_name: str,
    resolution: str,
    duration: float | None,
    video_metadata: dict[str, Any] | None = None,
) -> None:
    """Persist the generated video artifact and register metadata."""

    Asset.objects.filter(run=run, step=step, kind=PromptKind.VIDEO).delete()

    metadata: dict[str, Any] = {
        "provider": provider,
        "model": model_name,
        "resolution": resolution.lower(),
        "prompt_id": str(prompt.id),
    }
    if duration is not None:
        metadata["duration_seconds"] = round(float(duration), 2)
    if video_metadata:
        metadata.update(video_metadata)

    asset = Asset(
        run=run,
        step=step,
        kind=PromptKind.VIDEO,
        title=f"{run.title} - Video spot",
        metadata=metadata,
    )
    asset.file.save("video.mp4", ContentFile(payload), save=True)


def _finish_video_step(run: Run, step: Step, detail: str) -> None:
    """Mark the video step as completed and refresh run state."""

    with transaction.atomic():
        managed = Step.objects.select_for_update().get(id=step.id)
        managed.status = StepStatus.COMPLETED
        managed.detail = detail
        managed.finished_at = timezone.now()
        managed.save(update_fields=["status", "detail", "finished_at", "updated_at"])

    run.refresh_from_db()
    _maybe_finalize_run(run)


def _download_video_bytes(client: Any, video_handle: Any) -> bytes | None:
    """Download the generated video into memory using the Google client abstraction."""

    try:
        client.files.download(file=video_handle)
    except Exception:  # pragma: no cover - network failure path
        logger.exception("Failed to download Veo video file")
        return None

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
        temp_path = tmp_file.name

    try:
        video_handle.save(temp_path)
        with open(temp_path, "rb") as fp:
            return fp.read()
    except Exception:  # pragma: no cover - disk IO failure
        logger.exception("Failed to persist Veo video locally")
        return None
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


@shared_task(bind=True, ignore_result=True)
def generate_video_for_run(self, run_id: str) -> None:
    """Invoke Google Veo via Google AI Studio to create the requested video asset."""

    try:
        run = Run.objects.get(id=run_id)
    except Run.DoesNotExist:
        logger.warning("Run %s disappeared before video generation", run_id)
        return

    try:
        step = Step.objects.get(run=run, kind=StepKind.VIDEO)
    except Step.DoesNotExist:
        logger.error("Video step missing for run %s", run_id)
        return

    prompt = Prompt.objects.filter(run=run, kind=PromptKind.VIDEO).order_by("-created_at").first()
    if not prompt:
        _mark_step_failed(run, step, "No stored video prompt to process.")
        return

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
        managed = Step.objects.select_for_update().get(id=step.id)
        managed.status = StepStatus.RUNNING
        managed.started_at = managed.started_at or timezone.now()
        managed.detail = f"Rendering video via {model_name} at {resolution.upper()} with Veo."
        managed.save(update_fields=["status", "started_at", "detail", "updated_at"])

    api_key = getattr(settings, "GOOGLE_API_KEY", "")
    if not api_key:
        logger.error("Google API key is not configured; cannot generate video for run %s", run_id)
        _mark_step_failed(run, step, "Google API key is not configured.")
        return

    try:
        client = genai.Client(api_key=api_key)
    except Exception:
        logger.exception("Failed to initialize Google Veo client for run %s", run_id)
        _mark_step_failed(run, step, "Could not initialize Google Veo client.")
        return

    config_kwargs: dict[str, Any] = {
        "resolution": resolution,
        "aspect_ratio": "16:9",
    }
    try:
        video_config = types.GenerateVideosConfig(**config_kwargs)
    except Exception:
        logger.exception("Failed to build Veo video configuration for run %s", run_id)
        _mark_step_failed(run, step, "Could not configure Google Veo request.")
        return

    try:
        operation = client.models.generate_videos(
            model=model_name,
            prompt=prompt.content,
            config=video_config,
        )
    except Exception:  # pragma: no cover - API invocation failure
        logger.exception("Google Veo generation failed for run %s", run_id)
        _mark_step_failed(run, step, "Google Veo API error during video generation.")
        return

    poll_interval = getattr(settings, "GOOGLE_VEO_POLL_INTERVAL", 5)

    try:
        while not getattr(operation, "done", False):
            time.sleep(max(1, poll_interval))
            operation = client.operations.get(operation)
    except Exception:  # pragma: no cover - polling failure
        logger.exception("Polling Veo operation failed for run %s", run_id)
        _mark_step_failed(run, step, "Failed while polling Google Veo for completion.")
        return

    response = getattr(operation, "response", None)
    if not response or not getattr(response, "generated_videos", None):
        logger.error("Veo returned no generated videos for run %s", run_id)
        _mark_step_failed(run, step, "Google Veo returned an empty response.")
        return

    generated_video = response.generated_videos[0]
    video_handle = getattr(generated_video, "video", None)
    if video_handle is None:
        logger.error("Veo response missing video handle for run %s", run_id)
        _mark_step_failed(run, step, "Google Veo response missing video handle.")
        return

    video_bytes = _download_video_bytes(client, video_handle)
    if not video_bytes:
        _mark_step_failed(run, step, "Could not download generated video content.")
        return

    duration = getattr(generated_video, "duration_seconds", None)
    if duration is None:
        duration = getattr(video_handle, "duration_seconds", None)

    extra_metadata: dict[str, Any] = {}
    mime_type = getattr(video_handle, "mime_type", None)
    if mime_type:
        extra_metadata["mime_type"] = mime_type
    thumbnail = getattr(generated_video, "thumbnail", None)
    if thumbnail is not None:
        thumb_path: str | None = None
        try:
            client.files.download(file=thumbnail)
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_thumb:
                thumb_path = tmp_thumb.name
            thumbnail.save(thumb_path)
            with open(thumb_path, "rb") as thumb_fp:
                thumb_bytes = thumb_fp.read()
            extra_metadata["poster_inline_base64"] = base64.b64encode(thumb_bytes).decode("ascii")
        except Exception:
            logger.warning("Unable to download Veo thumbnail for run %s", run_id)
        finally:
            if thumb_path:
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass

    _save_video_asset(
        run,
        step,
        prompt,
        payload=video_bytes,
        provider="google",
        model_name=model_name,
        resolution=resolution,
        duration=duration,
        video_metadata=extra_metadata,
    )

    _finish_video_step(
        run,
        step,
        f"Generated Veo video at {resolution.upper()} using {model_name}.",
    )
