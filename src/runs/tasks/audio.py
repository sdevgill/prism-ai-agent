from __future__ import annotations

import io
import logging
import math
import wave
from typing import Any

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


def _create_mock_audio_clip(seconds: float = 2.0) -> tuple[bytes, float]:
    """Generate a simple sine-wave WAV clip for offline demos."""

    sample_rate = 22050
    amplitude = 12000
    total_samples = max(1, int(sample_rate * seconds))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for index in range(total_samples):
            sample = int(amplitude * math.sin(2 * math.pi * index / sample_rate))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav_file.writeframes(bytes(frames))
    return buffer.getvalue(), seconds


def _save_audio_asset(
    run: Run,
    step: Step,
    prompt: Prompt,
    *,
    binary: bytes,
    provider: str,
    model_name: str,
    voice: str,
    audio_format: str,
    duration: float | None,
    mock: bool,
) -> None:
    """Persist the audio artifact to storage and register metadata."""

    extension = (audio_format or "wav").lower()
    if extension == "mp3":
        filename = "audio.mp3"
    elif extension == "wav":
        filename = "audio.wav"
    else:
        filename = f"audio.{extension}"

    Asset.objects.filter(run=run, step=step, kind=PromptKind.AUDIO).delete()

    metadata: dict[str, Any] = {
        "provider": provider,
        "model": model_name,
        "voice": voice,
        "format": extension,
        "prompt_id": str(prompt.id),
    }
    if duration is not None:
        metadata["duration_seconds"] = round(float(duration), 2)
    if mock:
        metadata["mock"] = True

    asset = Asset(
        run=run,
        step=step,
        kind=PromptKind.AUDIO,
        title=f"{run.title} - Audio narration",
        metadata=metadata,
    )
    asset.file.save(filename, ContentFile(binary), save=True)


def _finish_audio_step(
    run: Run,
    step: Step,
    detail: str,
) -> None:
    """Mark the audio step as completed and re-evaluate run state."""

    with transaction.atomic():
        managed = Step.objects.select_for_update().get(id=step.id)
        managed.status = StepStatus.COMPLETED
        managed.detail = detail
        managed.finished_at = timezone.now()
        managed.save(update_fields=["status", "detail", "finished_at", "updated_at"])

    run.refresh_from_db()
    _maybe_finalize_run(run)


def _complete_audio_with_mock(
    run: Run,
    step: Step,
    prompt: Prompt,
    voice: str,
    *,
    reason: str | None = None,
) -> None:
    """Generate a placeholder narration when real synthesis is unavailable."""

    mock_audio, seconds = _create_mock_audio_clip(2.5)
    detail = "Generated placeholder narration (mock WAV)."
    if reason:
        detail = f"{detail} {reason}".strip()
    _save_audio_asset(
        run,
        step,
        prompt,
        binary=mock_audio,
        provider="mock",
        model_name=f"{getattr(settings, 'OPENAI_AUDIO_MODEL', 'gpt-4o-mini-tts')} (mock)",
        voice=voice,
        audio_format="wav",
        duration=seconds,
        mock=True,
    )
    _finish_audio_step(run, step, detail)


@shared_task(bind=True, ignore_result=True)
def generate_audio_for_run(self, run_id: str) -> None:
    """Call OpenAI text-to-speech to create narration assets for a run."""

    try:
        run = Run.objects.get(id=run_id)
    except Run.DoesNotExist:
        logger.warning("Run %s disappeared before audio generation", run_id)
        return

    try:
        step = Step.objects.get(run=run, kind=StepKind.AUDIO)
    except Step.DoesNotExist:
        logger.error("Audio step missing for run %s", run_id)
        return

    prompt = Prompt.objects.filter(run=run, kind=PromptKind.AUDIO).order_by("-created_at").first()
    if not prompt:
        _mark_step_failed(run, step, "No stored audio prompt to process.")
        return

    options = (run.params or {}).get("audio") or {}
    voice = (options.get("voice") or getattr(settings, "OPENAI_AUDIO_VOICE", "ash")).lower()
    audio_format = (
        options.get("format") or getattr(settings, "OPENAI_AUDIO_FORMAT", "mp3")
    ).lower()
    model_name = getattr(settings, "OPENAI_AUDIO_MODEL", "gpt-4o-mini-tts")
    system_prompt = getattr(
        settings,
        "OPENAI_AUDIO_SYSTEM_PROMPT",
        "Speak in an emotive and friendly tone.",
    )

    with transaction.atomic():
        managed = Step.objects.select_for_update().get(id=step.id)
        managed.status = StepStatus.RUNNING
        managed.started_at = managed.started_at or timezone.now()
        managed.detail = (
            f"Rendering narration via {model_name} ({voice.title()} • {audio_format.upper()})."
        )
        managed.save(update_fields=["status", "started_at", "detail", "updated_at"])

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        logger.info("OpenAI API key missing, using mock audio for run %s", run_id)
        _complete_audio_with_mock(
            run,
            step,
            prompt,
            voice,
            reason="No API key configured.",
        )
        return

    client = OpenAI(api_key=api_key)

    response_kwargs: dict[str, object] = {
        "model": model_name,
        "voice": voice,
        "input": prompt.content,
        "response_format": audio_format,
    }
    if system_prompt:
        response_kwargs["instructions"] = system_prompt

    try:
        response = client.audio.speech.create(**response_kwargs)
    except OpenAIError as exc:
        logger.exception("OpenAI audio generation failed for run %s", run_id)
        _complete_audio_with_mock(
            run,
            step,
            prompt,
            voice,
            reason=f"Fell back after OpenAI error: {exc}",
        )
        return

    binary: bytes | None = None
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        binary = bytes(content)
    else:
        iterator = getattr(response, "iter_bytes", None)
        if callable(iterator):
            buffer = io.BytesIO()
            for chunk in iterator(chunk_size=4096):
                if chunk:
                    buffer.write(chunk)
            binary = buffer.getvalue() or None
        else:
            reader = getattr(response, "read", None)
            if callable(reader):
                data = reader()
                if isinstance(data, str):
                    data = data.encode("utf-8")
                if data:
                    binary = bytes(data)

    if not binary:
        logger.error("OpenAI returned no audio data for run %s", run_id)
        _complete_audio_with_mock(
            run,
            step,
            prompt,
            voice,
            reason="OpenAI returned no audio data.",
        )
        return

    duration_value: float | None = None
    http_response = getattr(response, "response", None)
    if http_response is not None:
        headers = getattr(http_response, "headers", None) or {}
        raw_duration = headers.get("x-openai-audio-duration-seconds") or headers.get(
            "x-openai-audio-duration"
        )
        if raw_duration:
            try:
                duration_value = float(raw_duration)
            except (TypeError, ValueError):
                duration_value = None

    _save_audio_asset(
        run,
        step,
        prompt,
        binary=binary,
        provider="openai",
        model_name=model_name,
        voice=voice,
        audio_format=audio_format,
        duration=duration_value,
        mock=False,
    )

    duration_note = f" ~{duration_value:.1f}s" if duration_value is not None else ""
    detail = (
        f"Saved narration via {model_name} "
        f"({voice.title()} • {audio_format.upper()})"
        f"{duration_note}."
    )
    _finish_audio_step(run, step, detail)
