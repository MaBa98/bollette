"""Upload bollette: il file non tocca MAI il disco.

Perché NON usiamo `UploadFile` + multipart: Starlette accumula i file multipart in uno
SpooledTemporaryFile che oltre 1 MB viene riversato su /tmp. Su un PaaS il disco è effimero, ma
il vincolo è "zero disco", e una bolletta da 2-3 MB lo violerebbe. Quindi il client invia il PDF
come body grezzo (Content-Type: application/pdf) e noi leggiamo lo stream a chunk in un
bytearray, con limite di dimensione applicato DURANTE la lettura (niente DoS da body enormi).
"""

from __future__ import annotations

import hashlib
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.api.schemas import InvoiceOut
from app.core.config import Settings, get_settings
from app.core.security import CurrentUserDep
from app.db.repositories import InvoiceRepoDep
from app.db.session import SessionDep
from app.parsing.contracts import Commodity, DocumentInput, ExtractionError, UnsupportedDocumentError
from app.parsing.pipeline import InvoiceExtractionPipeline, get_pipeline

router = APIRouter(prefix="/invoices", tags=["invoices"])

ACCEPTED_CONTENT_TYPES = frozenset({
    "application/pdf",
    # FUTURE-PROOF: aggiungere "image/jpeg", "image/png" quando OCRImageParser sarà registrato.
})


async def _read_body_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and int(declared) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File oltre {limit // 1_048_576} MB")
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > limit:  # Content-Length può mentire o mancare (chunked)
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File troppo grande")
    if not buffer:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body vuoto")
    return bytes(buffer)


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    request: Request,
    user: CurrentUserDep,
    repo: InvoiceRepoDep,
    session: SessionDep,
    pipeline: Annotated[InvoiceExtractionPipeline, Depends(get_pipeline)],
    settings: Annotated[Settings, Depends(get_settings)],
    content_type: Annotated[str, Header()],
    x_filename: Annotated[str, Header()] = "bolletta.pdf",
) -> InvoiceOut:
    media_type = content_type.split(";")[0].strip().lower()
    if media_type not in ACCEPTED_CONTENT_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Carica un PDF")

    content = await _read_body_limited(request, settings.max_upload_bytes)
    document = DocumentInput(content=content, filename=unquote(x_filename)[:255], declared_mime=media_type)
    file_sha256 = hashlib.sha256(content).hexdigest()

    await repo.ensure_user(user.email)
    if await repo.exists_by_hash(file_sha256):
        raise HTTPException(status.HTTP_409_CONFLICT, "Hai già caricato questa bolletta")

    try:
        # Parsing CPU-bound fuori dall'event loop, altrimenti blocca tutte le altre richieste.
        parsed = await run_in_threadpool(pipeline.run, document)
    except UnsupportedDocumentError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except (ExtractionError, ValueError) as exc:  # ValueError: validazione di ParsedInvoice
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Bolletta non leggibile: {exc}") from exc
    finally:
        del content, document  # rilascia subito i byte del PDF

    # FUTURE-PROOF (opzionale): archiviare l'originale su Supabase Storage, bucket privato,
    # path f"{user.id}/{file_sha256}.pdf", solo con consenso esplicito dell'utente.

    try:
        invoice = await repo.create(parsed, file_sha256)
        await session.commit()
    except IntegrityError as exc:  # upload concorrente dello stesso file
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Hai già caricato questa bolletta") from exc
    return InvoiceOut.model_validate(invoice)


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(repo: InvoiceRepoDep, commodity: Commodity | None = None) -> list[InvoiceOut]:
    return [InvoiceOut.model_validate(i) for i in await repo.list(commodity)]


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(invoice_id: UUID, repo: InvoiceRepoDep, session: SessionDep) -> Response:
    if not await repo.delete(invoice_id):  # 404 anche se è di un altro utente: niente enumeration
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bolletta non trovata")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
