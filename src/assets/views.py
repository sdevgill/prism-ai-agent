from collections import defaultdict
from types import SimpleNamespace

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import TemplateView

from src.assets.models import Asset
from src.runs.models import PromptKind


class AssetLibraryView(LoginRequiredMixin, TemplateView):
    """Render the library of generated assets for the authenticated user."""

    template_name = "assets/library.html"

    def get_context_data(self, **kwargs):
        """Filter assets by the current user and optional query parameters."""

        context = super().get_context_data(**kwargs)
        queryset = Asset.objects.select_related("run", "step", "run__owner").filter(
            run__owner=self.request.user
        )

        query = self.request.GET.get("q")
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(run__title__icontains=query)
                | Q(run__submitted_url__icontains=query)
            )

        source_filter = self.request.GET.get("source")
        if source_filter:
            queryset = queryset.filter(
                Q(run__title__icontains=source_filter)
                | Q(run__submitted_url__icontains=source_filter)
            )

        assets_by_kind: dict[str, list[Asset]] = defaultdict(list)
        for asset in queryset.order_by("-created_at"):
            assets_by_kind[asset.kind].append(asset)

        context.update(
            {
                "assets_by_modality": SimpleNamespace(
                    image=assets_by_kind.get(PromptKind.IMAGE, []),
                    audio=assets_by_kind.get(PromptKind.AUDIO, []),
                    video=assets_by_kind.get(PromptKind.VIDEO, []),
                ),
                "image_assets": assets_by_kind.get(PromptKind.IMAGE, []),
                "audio_assets": assets_by_kind.get(PromptKind.AUDIO, []),
                "video_assets": assets_by_kind.get(PromptKind.VIDEO, []),
            }
        )
        return context
