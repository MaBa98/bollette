"""FUTURE-PROOF: segnaposto della strategia OCR. NON registrata nel resolver.

Esiste per fissare oggi il contratto, così il giorno in cui serve basta implementare `extract`
e aggiungere un'istanza in `resolver.build_default_resolver()`.

Note di dimensionamento (da leggere prima di implementarla):
  * Tesseract/PaddleOCR in-process su un'istanza free da 512 MB è al limite: un'immagine da
    12 MP decodificata occupa ~36 MB, i modelli PaddleOCR ~150 MB. Valutare un servizio esterno
    (Google Document AI, Azure Document Intelligence, AWS Textract) dietro questa stessa interfaccia.
  * L'OCR è lento (secondi): andrà spostato su BackgroundTasks come lo scraping,
    con stato `processing` sulla bolletta.
"""

from __future__ import annotations

from typing import ClassVar

from app.parsing.contracts import DocumentInput, ExtractedText, SourceKind
from app.parsing.strategies.base import DocumentParser

_IMAGE_MIMES = frozenset({"image/jpeg", "image/png"})


class OCRImageParser(DocumentParser):
    source_kind: ClassVar[SourceKind] = SourceKind.IMAGE
    priority: ClassVar[int] = 50  # dopo NativePDFParser: i PDF con testo non devono mai finire in OCR

    def can_handle(self, document: DocumentInput) -> bool:
        # FUTURE-PROOF: accettare anche i PDF scansionati (sniffed_mime == application/pdf
        # senza layer di testo), restituendo source_kind=SCANNED_PDF in quel caso.
        return document.sniffed_mime in _IMAGE_MIMES

    def extract(self, document: DocumentInput) -> ExtractedText:
        # FUTURE-PROOF: 1) deskew/binarizzazione  2) OCR per pagina  3) media delle confidence
        # return ExtractedText(pages=..., source_kind=SourceKind.IMAGE, ocr_confidence=0.87)
        raise NotImplementedError("OCR non ancora abilitato")
