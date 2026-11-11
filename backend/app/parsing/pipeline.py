"""Facade del sottosistema di parsing: l'unica classe che l'API conosce."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.parsing.contracts import DocumentInput, ParsedInvoice
from app.parsing.strategies.resolver import ParserResolver, build_default_resolver
from app.parsing.templates.registry import TemplateRegistry, build_default_registry


@dataclass(frozen=True, slots=True)
class InvoiceExtractionPipeline:
    resolver: ParserResolver
    templates: TemplateRegistry

    def run(self, document: DocumentInput) -> ParsedInvoice:
        """Sincrono e CPU-bound: chiamarlo con `run_in_threadpool` dall'event loop."""
        strategy = self.resolver.resolve(document)      # Strategy: bytes → testo
        extracted = strategy.extract(document)
        template = self.templates.detect(extracted.full_text)
        return template.parse(extracted)                # Template Method: testo → bolletta


@lru_cache(maxsize=1)
def get_pipeline() -> InvoiceExtractionPipeline:
    """Dependency FastAPI. Stateless e thread-safe: un'istanza per processo."""
    return InvoiceExtractionPipeline(resolver=build_default_resolver(), templates=build_default_registry())
