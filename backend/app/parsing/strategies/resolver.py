"""Context dello Strategy Pattern: sceglie la strategia giusta per il documento."""

from __future__ import annotations

from collections.abc import Iterable

from app.parsing.contracts import DocumentInput, UnsupportedDocumentError
from app.parsing.strategies.base import DocumentParser
from app.parsing.strategies.native_pdf import NativePDFParser


class ParserResolver:
    def __init__(self, parsers: Iterable[DocumentParser]) -> None:
        self._parsers: tuple[DocumentParser, ...] = tuple(sorted(parsers, key=lambda p: p.priority))
        if not self._parsers:
            raise ValueError("Serve almeno una strategia di parsing")

    def resolve(self, document: DocumentInput) -> DocumentParser:
        for parser in self._parsers:
            if parser.can_handle(document):
                return parser
        raise UnsupportedDocumentError(
            "Formato non supportato: serve un PDF nativo (scaricato dall'area clienti), "
            "non una scansione o una foto."
        )


def build_default_resolver() -> ParserResolver:
    return ParserResolver(
        [
            NativePDFParser(),
            # FUTURE-PROOF: attivare l'OCR è questa sola riga (più l'implementazione di extract):
            # OCRImageParser(),
        ]
    )
