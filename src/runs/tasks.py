from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from openai import OpenAI, OpenAIError

from celery import shared_task
from src.runs.models import (
    Prompt,
    Run,
    RunStatus,
    Step,
    StepKind,
    StepStatus,
)

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
    user_blocks: list[Dict[str, Any]] = [
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
        truncated = source_text.strip()
        if len(truncated) > 6000:
            truncated = f"{truncated[:6000]}\n[truncated]"
        user_blocks.append(
            {
                "type": "input_text",
                "text": f"Direct source text (may be truncated):\n{truncated}",
            }
        )

    request_kwargs: Dict[str, Any] = {}
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
            Prompt.objects.update_or_create(
                run=run,
                kind=modality,
                defaults={
                    "content": prompt_text,
                    "metadata": {"raw_response": payload},
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

        run.status = RunStatus.COMPLETED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at"])


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
