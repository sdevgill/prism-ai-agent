from typing import List

from django import forms

from src.runs.models import PromptKind


class RunRequestForm(forms.Form):
    """Collect the minimal data required to queue a new run."""

    INPUT_URL = "url"
    INPUT_TEXT = "text"
    INPUT_CHOICES = (
        (INPUT_URL, "Website URL"),
        (INPUT_TEXT, "Pasted text"),
    )

    run_title = forms.CharField(max_length=160)
    input_mode = forms.ChoiceField(choices=INPUT_CHOICES)
    source_url = forms.URLField(required=False)
    source_text = forms.CharField(widget=forms.Textarea, required=False)
    modalities = forms.MultipleChoiceField(
        choices=PromptKind.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    def clean_modalities(self) -> List[str]:
        """Ensure at least one modality is selected."""

        data = self.cleaned_data.get("modalities", [])
        if not data:
            raise forms.ValidationError("Pick at least one output format.")
        return data

    def clean(self):
        """Validate that the correct input field is supplied for the chosen mode."""

        cleaned = super().clean()
        mode = cleaned.get("input_mode")
        url = cleaned.get("source_url")
        text = cleaned.get("source_text")

        if mode == self.INPUT_URL and not url:
            self.add_error("source_url", "Provide a URL to ingest.")
        elif mode == self.INPUT_TEXT and not text:
            self.add_error("source_text", "Paste the text Prism should analyze.")

        return cleaned
