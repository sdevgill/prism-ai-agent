from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from src.runs.models import PromptKind, Run, RunStatus, Step, StepKind, StepStatus

__all__ = [
    "_mark_run_failed",
    "_expected_step_kinds",
    "_maybe_finalize_run",
    "_mark_step_failed",
]


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


def _expected_step_kinds(run: Run) -> set[str]:
    """Return the set of step kinds required for the run to finish."""

    kinds: set[str] = {StepKind.ANALYZE}
    modalities = set(run.requested_modalities or [])
    if PromptKind.IMAGE in modalities:
        kinds.add(StepKind.IMAGE)
    if PromptKind.AUDIO in modalities:
        kinds.add(StepKind.AUDIO)
    # Video will be appended here once implemented.
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
