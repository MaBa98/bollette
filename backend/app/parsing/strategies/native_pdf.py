"""Strategia per PDF nativi (con layer di testo), il caso del 95% delle bollette scaricate dall'area clienti."""

from __future__ import annotations

import io
import logging
from typing import ClassVar

import pymupdf as fitz
import pdfplumber

from app.parsing.contracts import DocumentInput, ExtractedText, ExtractionError, SourceKind
from app.parsing.strategies.base import DocumentParser

logger = logging.getLogger(__name__)


class NativePDFParser(DocumentParser):
    """PyMuPDF per il controllo rapido del layer di testo, pdfplumber per l'estrazione.

    PyMuPDF è ~10x più veloce nel sondare le prime pagine; pdfplumber dà un ordine di lettura
    più stabile sulle tabelle delle bollette. Usare entrambi costa poco perché tutto è in RAM.
    """

    source_kind: ClassVar[SourceKind] = SourceKind.NATIVE_PDF
    priority: ClassVar[int] = 10

    MAX_PAGES: ClassVar[int] = 30
    #: Sotto questa soglia di caratteri per pagina il PDF è quasi certamente una scansione.
    MIN_CHARS_PER_PAGE: ClassVar[int] = 80
    PROBE_PAGES: ClassVar[int] = 2

    def can_handle(self, document: DocumentInput) -> bool:
        if document.sniffed_mime != "application/pdf":
            return False
        try:
            return self._has_text_layer(document.content)
        except (fitz.FileDataError, RuntimeError) as exc:  # PDF corrotto
            logger.info("PDF non apribile da PyMuPDF: %s", exc)
            return False

    def _has_text_layer(self, content: bytes) -> bool:
        with fitz.open(stream=content, filetype="pdf") as doc:
            if doc.needs_pass:
                # Bollette protette da password: non le gestiamo, nessuna strategia le accetterà.
                return False
            probe = range(min(self.PROBE_PAGES, doc.page_count))
            chars = sum(len(doc[i].get_text("text").strip()) for i in probe)
            pages = max(len(probe), 1)
        # FUTURE-PROOF: un PDF che fallisce questo controllo è una scansione. Oggi viene rifiutato
        # (415); quando registreremo OCRImageParser con priority > 10 lo intercetterà lui,
        # senza alcuna modifica a questa classe.
        return chars / pages >= self.MIN_CHARS_PER_PAGE

    def extract(self, document: DocumentInput) -> ExtractedText:
        with pdfplumber.open(io.BytesIO(document.content)) as pdf:
            if len(pdf.pages) > self.MAX_PAGES:
                raise ExtractionError(f"Documento troppo lungo ({len(pdf.pages)} pagine)")
            pages = tuple(
                page.extract_text(x_tolerance=1.5, y_tolerance=3) or "" for page in pdf.pages
            )
        if not any(p.strip() for p in pages):
            raise ExtractionError("Nessun testo estraibile dal PDF")
        return ExtractedText(pages=pages, source_kind=self.source_kind)
