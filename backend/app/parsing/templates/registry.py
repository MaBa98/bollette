from __future__ import annotations

from collections.abc import Sequence

from app.parsing.templates.base import BaseProviderTemplate
from app.parsing.templates.providers import EnelTemplate, GenericItalianTemplate, PlenitudeTemplate


class TemplateRegistry:
    """Seleziona il template del fornitore; il fallback generico va sempre per ultimo."""

    def __init__(self, templates: Sequence[BaseProviderTemplate], fallback: BaseProviderTemplate) -> None:
        self._templates = tuple(templates)
        self._fallback = fallback

    def detect(self, text: str) -> BaseProviderTemplate:
        for template in self._templates:
            if template.matches(text):
                return template
        return self._fallback


def build_default_registry() -> TemplateRegistry:
    return TemplateRegistry(
        templates=[EnelTemplate(), PlenitudeTemplate()],  # aggiungere qui A2A, Edison, Hera, Iren...
        fallback=GenericItalianTemplate(),
    )
