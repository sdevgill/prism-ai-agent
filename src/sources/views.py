"""Views for source intake and orchestration kickoff."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, TemplateView

from src.runs.models import PromptKind, Run, RunStatus, Step, StepKind, StepStatus
from src.runs.tasks import generate_prompts_for_run
from src.runs.tokenization import PROMPT_TOKEN_LIMIT, count_tokens
from src.sources.forms import SOURCE_TEXT_MAX_LENGTH, RunRequestForm


class SourceIngestView(LoginRequiredMixin, FormView):
    """
    Capture user intent for a new run and dispatch background orchestration.
    """

    template_name = "sources/ingest.html"
    form_class = RunRequestForm
    success_url = reverse_lazy("sources:ingest")

    def get_context_data(self, **kwargs):
        """Expose status URL when redirected with a run querystring."""

        context = super().get_context_data(**kwargs)
        run_id = self.request.GET.get("run")
        if run_id and self.request.user.is_authenticated:
            try:
                run = Run.objects.get(id=run_id, owner=self.request.user)
            except Run.DoesNotExist:
                context["status_url"] = ""
            else:
                context["status_url"] = reverse("sources:run-status", args=[run.id])
        context.setdefault("prompt_token_limit", PROMPT_TOKEN_LIMIT)
        context.setdefault("prompt_token_limit_display", f"{PROMPT_TOKEN_LIMIT:,}")
        context.setdefault("max_source_text_chars", SOURCE_TEXT_MAX_LENGTH)
        context.setdefault("max_source_text_chars_display", f"{SOURCE_TEXT_MAX_LENGTH:,}")
        return context

    def form_valid(self, form):
        """
        Persist the run record, queue Celery work, and return a status snippet.
        """

        cleaned = form.cleaned_data
        image_options = cleaned.get("image_options")
        run = Run.objects.create(
            owner=self.request.user,
            title=cleaned["run_title"],
            submitted_url=cleaned.get("source_url", "")
            if cleaned["input_mode"] == RunRequestForm.INPUT_URL
            else "",
            requested_modalities=cleaned["modalities"],
            params={
                "input_mode": cleaned["input_mode"],
                "image": image_options,
            },
        )
        step, _ = Step.objects.get_or_create(run=run, kind=StepKind.ANALYZE)
        step.status = StepStatus.PENDING
        step.detail = "Queued for prompt generation via OpenAI."
        step.save(update_fields=["status", "detail", "updated_at"])

        payload = {
            "title": run.title,
            "submitted_url": run.submitted_url or None,
            "source_text": cleaned.get("source_text", "") or None,
            "modalities": cleaned["modalities"],
            "image_options": image_options,
        }
        generate_prompts_for_run.delay(str(run.id), **payload)

        status_url = reverse("sources:run-status", args=[run.id])
        context = {
            "run": run,
            "modalities": cleaned["modalities"],
            "timestamp": timezone.now(),
            "status_url": status_url,
        }
        html = render_to_string(
            "sources/partials/status_success.html",
            context,
            request=self.request,
        )
        if self.request.headers.get("HX-Request"):
            return HttpResponse(html)
        return redirect(f"{reverse('sources:ingest')}?run={run.id}")

    def form_invalid(self, form):
        """Return validation feedback inline for HTMX submissions."""

        if self.request.headers.get("HX-Request"):
            html = render_to_string(
                "sources/partials/status_error.html",
                {"form": form},
                request=self.request,
            )
            return HttpResponse(html, status=400)
        return super().form_invalid(form)


class RunStatusFragmentView(LoginRequiredMixin, TemplateView):
    """Return an HTMX-friendly fragment describing the latest run progress."""

    template_name = "sources/partials/run_status.html"
    http_method_names = ["get"]

    STEP_BADGES = {
        StepStatus.PENDING: "bg-slate-100 text-slate-700",
        StepStatus.RUNNING: "bg-blue-100 text-blue-600",
        StepStatus.COMPLETED: "bg-green-100 text-green-700",
        StepStatus.FAILED: "bg-red-100 text-red-600",
    }

    RUN_BADGES = {
        RunStatus.PENDING: "bg-slate-100 text-slate-700",
        RunStatus.RUNNING: "bg-blue-100 text-blue-600",
        RunStatus.COMPLETED: "bg-green-100 text-green-700",
        RunStatus.FAILED: "bg-red-100 text-red-600",
    }

    def get_context_data(self, **kwargs):
        """Fetch the run, associated steps, and presentation helpers."""

        context = super().get_context_data(**kwargs)
        run = get_object_or_404(
            Run.objects.prefetch_related("steps"),
            id=self.kwargs["pk"],
            owner=self.request.user,
        )
        steps = list(run.steps.order_by("created_at"))
        steps_by_kind = {step.kind: step for step in steps}
        for step in steps:
            step.badge_class = self.STEP_BADGES.get(step.status, "bg-slate-100 text-slate-700")

        modality_labels = []
        for modality in run.requested_modalities or []:
            try:
                modality_labels.append(PromptKind(modality).label)
            except ValueError:
                modality_labels.append(modality.title())

        poll_active = run.status not in {RunStatus.COMPLETED, RunStatus.FAILED}

        status_message = self._build_status_message(run, steps_by_kind)

        context.update(
            {
                "run": run,
                "steps": steps,
                "status_url": self.request.path,
                "modality_labels": modality_labels,
                "run_badge_class": self.RUN_BADGES.get(run.status, "bg-slate-100 text-slate-700"),
                "poll_active": poll_active,
                "status_message": status_message,
            }
        )
        return context

    def _build_status_message(self, run: Run, steps_by_kind: dict[str, Step]) -> str:
        """Produce a user-friendly status summary for the run."""

        if run.status == RunStatus.COMPLETED:
            return "Assets are generated, please visit the Assets page."
        if run.status == RunStatus.FAILED:
            failed_steps = [
                step
                for step in steps_by_kind.values()
                if step.status == StepStatus.FAILED and step.detail
            ]
            detail = failed_steps[0].detail if failed_steps else "Something went wrong."
            return f"Generation failed — {detail}"

        analyze_step = steps_by_kind.get(StepKind.ANALYZE)
        image_step = steps_by_kind.get(StepKind.IMAGE)

        if not analyze_step or analyze_step.status != StepStatus.COMPLETED:
            return "Starting assets generation..."

        if image_step and image_step.status != StepStatus.COMPLETED:
            return "Assets generation in progress...."

        return "Assets generation in progress...."


class TokenEstimateView(LoginRequiredMixin, View):
    """
    Return the token count for arbitrary pasted text.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        """Compute and respond with the model-aware token count."""

        text = request.POST.get("text", "")
        tokens = count_tokens(text)
        return JsonResponse(
            {
                "tokens": tokens,
                "limit": PROMPT_TOKEN_LIMIT,
            }
        )
