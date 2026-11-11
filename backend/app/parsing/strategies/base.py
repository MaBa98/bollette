"""Strategy Pattern: interfaccia comune a tutti gli estrattori di testo."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.parsing.contracts import DocumentInput, ExtractedText, SourceKind


class DocumentParser(ABC):
    """Una strategia trasforma un documento (bytes in RAM) in testo grezzo per pagina.

    Contratto:
      * `can_handle` deve essere economico (niente estrazione completa) e senza side effect.
      * `extract` è sincrono e CPU-bound: il chiamante lo esegue in un threadpool.
      * Nessuna strategia scrive su disco: lavora solo su `document.content`.
    """

    source_kind: ClassVar[SourceKind]
    #: Priorità di risoluzione: valori più bassi vengono provati prima.
    priority: ClassVar[int] = 100

    @abstractmethod
    def can_handle(self, document: DocumentInput) -> bool: ...

    @abstractmethod
    def extract(self, document: DocumentInput) -> ExtractedText: ...
