from django import forms
from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError

from src.runs.models import PromptKind

SOURCE_TEXT_MAX_LENGTH = 50_000


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
    source_url = forms.CharField(required=False)
    source_text = forms.CharField(
        max_length=SOURCE_TEXT_MAX_LENGTH, widget=forms.Textarea, required=False
    )
    modalities = forms.MultipleChoiceField(
        choices=PromptKind.choices,
        widget=forms.CheckboxSelectMultiple,
    )
    image_count = forms.TypedChoiceField(
        choices=((1, "1"), (2, "2"), (3, "3")),
        coerce=int,
        empty_value=3,
        required=False,
        initial=3,
        help_text="Number of images to generate when Image is selected.",
    )
    image_quality = forms.ChoiceField(
        choices=(
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
        ),
        required=False,
        initial="medium",
        help_text="Quality setting passed to the GPT-Image-1 model.",
    )
    image_size = forms.ChoiceField(
        choices=(
            ("1024x1024", "Square (1024 × 1024)"),
            ("1024x1536", "Portrait (1024 × 1536)"),
            ("1536x1024", "Landscape (1536 × 1024)"),
        ),
        required=False,
        initial="1024x1536",
        help_text="Image resolution supported by GPT-Image-1.",
    )

    audio_voice = forms.ChoiceField(
        choices=(
            ("ash", "Ash"),
            ("nova", "Nova"),
            ("ballad", "Ballad"),
        ),
        required=False,
        initial=getattr(settings, "OPENAI_AUDIO_VOICE", "ash"),
        help_text="Voice preset used for text-to-speech output.",
    )
    audio_format = forms.ChoiceField(
        choices=(
            ("mp3", "MP3"),
            ("wav", "WAV"),
        ),
        required=False,
        initial=getattr(settings, "OPENAI_AUDIO_FORMAT", "mp3"),
        help_text="Container format for the generated narration file.",
    )
    video_model = forms.ChoiceField(
        choices=(
            (
                getattr(
                    settings,
                    "GOOGLE_VEO_FAST_MODEL",
                    "veo-3.0-fast-generate-001",
                ),
                "Veo 3 Fast",
            ),
            (
                getattr(settings, "GOOGLE_VEO_MODEL", "veo-3.0-generate-001"),
                "Veo 3",
            ),
        ),
        required=False,
        initial=getattr(
            settings,
            "GOOGLE_VEO_FAST_MODEL",
            "veo-3.0-fast-generate-001",
        ),
        help_text="Google Veo model to render the short-form video.",
    )
    video_resolution = forms.ChoiceField(
        choices=(
            ("720p", "720p"),
            ("1080p", "1080p"),
        ),
        required=False,
        initial=getattr(settings, "GOOGLE_VEO_DEFAULT_RESOLUTION", "720p"),
        help_text="Select between 720p and 1080p output.",
    )

    def clean_modalities(self) -> list[str]:
        """Ensure at least one modality is selected."""

        data = self.cleaned_data.get("modalities", [])
        if not data:
            raise forms.ValidationError("Pick at least one output format.")
        return data

    def clean(self):
        """Validate that the correct input field is supplied for the chosen mode."""

        cleaned = super().clean()
        mode = cleaned.get("input_mode")
        url = cleaned.get("source_url", "")
        text = cleaned.get("source_text")

        if mode == self.INPUT_URL and not url:
            self.add_error("source_url", "Provide a URL to ingest.")
        elif mode == self.INPUT_TEXT and not text:
            self.add_error("source_text", "Paste the text Prism should analyze.")

        if mode == self.INPUT_URL and url:
            normalized = url.strip()
            if normalized and not normalized.startswith(("http://", "https://")):
                normalized = f"https://{normalized}"
            url_validator = validators.URLValidator()
            try:
                url_validator(normalized)
            except ValidationError:
                self.add_error("source_url", "Enter a valid URL.")
            else:
                cleaned["source_url"] = normalized

        modalities = cleaned.get("modalities", [])
        if PromptKind.IMAGE in modalities:
            count = cleaned.get("image_count")
            quality = cleaned.get("image_quality")
            size = cleaned.get("image_size")
            if count not in {1, 2, 3}:
                self.add_error("image_count", "Pick between 1 and 3 images.")
            if quality not in {"low", "medium", "high"}:
                self.add_error("image_quality", "Select a quality level.")
            if size not in {"1024x1024", "1024x1536", "1536x1024"}:
                self.add_error("image_size", "Pick an available size.")
            if "image_options" not in cleaned:
                cleaned["image_options"] = {
                    "count": count or 3,
                    "quality": quality
                    or getattr(
                        settings,
                        "OPENAI_IMAGE_QUALITY",
                        "medium",
                    ),
                    "size": size
                    or getattr(
                        settings,
                        "OPENAI_IMAGE_SIZE",
                        "1024x1536",
                    ),
                }
        else:
            cleaned["image_options"] = None

        if PromptKind.AUDIO in modalities:
            voice = (
                cleaned.get("audio_voice") or getattr(settings, "OPENAI_AUDIO_VOICE", "ash")
            ).lower()
            format_ = (
                cleaned.get("audio_format") or getattr(settings, "OPENAI_AUDIO_FORMAT", "mp3")
            ).lower()
            valid_voices = {"ash", "nova", "ballad"}
            valid_formats = {"mp3", "wav"}
            if voice not in valid_voices:
                self.add_error("audio_voice", "Choose an available voice preset.")
            if format_ not in valid_formats:
                self.add_error("audio_format", "Pick a supported audio format.")
            cleaned["audio_options"] = {"voice": voice, "format": format_}
        else:
            cleaned["audio_options"] = None

        if PromptKind.VIDEO in modalities:
            model_name = cleaned.get("video_model") or getattr(
                settings,
                "GOOGLE_VEO_FAST_MODEL",
                "veo-3.0-fast-generate-001",
            )
            resolution = (cleaned.get("video_resolution") or "720p").lower()
            valid_models = {
                getattr(settings, "GOOGLE_VEO_FAST_MODEL", "veo-3.0-fast-generate-001"),
                getattr(settings, "GOOGLE_VEO_MODEL", "veo-3.0-generate-001"),
            }
            if model_name not in valid_models:
                self.add_error("video_model", "Pick a supported Veo model.")
            if resolution not in {"720p", "1080p"}:
                self.add_error("video_resolution", "Choose 720p or 1080p.")
            cleaned["video_options"] = {
                "model": model_name,
                "resolution": resolution,
            }
        else:
            cleaned["video_options"] = None

        return cleaned
