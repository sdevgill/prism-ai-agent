"""Views for source intake and orchestration kickoff."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView

from src.runs.models import Run, Step, StepKind, StepStatus
from src.runs.tasks import generate_prompts_for_run
from src.sources.forms import RunRequestForm


class SourceIngestView(LoginRequiredMixin, FormView):
    """
    Capture user intent for a new run and dispatch background orchestration.
    """

    template_name = "sources/ingest.html"
    form_class = RunRequestForm
    success_url = reverse_lazy("sources:ingest")

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

        context = {
            "run": run,
            "modalities": cleaned["modalities"],
            "timestamp": timezone.now(),
        }
        html = render_to_string(
            "sources/partials/status_success.html",
            context,
            request=self.request,
        )
        if self.request.headers.get("HX-Request"):
            return HttpResponse(html)
        return super().form_valid(form)

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
