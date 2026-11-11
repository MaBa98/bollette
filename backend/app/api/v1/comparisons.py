from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from app.api.schemas import ComparisonRequest, ComparisonTaskOut
from app.core.security import CurrentUserDep
from app.db.repositories import TaskRepoDep
from app.db.session import SessionDep
from app.services.comparison import run_market_comparison

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


@router.post("", response_model=ComparisonTaskOut, status_code=status.HTTP_202_ACCEPTED)
async def start_comparison(
    body: ComparisonRequest,
    background: BackgroundTasks,
    response: Response,
    user: CurrentUserDep,
    repo: TaskRepoDep,
    session: SessionDep,
) -> ComparisonTaskOut:
    if active := await repo.find_active(body.commodity):
        response.status_code = status.HTTP_200_OK  # idempotente: restituisce il job già in corso
        return ComparisonTaskOut.model_validate(active)

    await repo.ensure_user(user.email)
    task = await repo.create(body.commodity)
    # Commit PRIMA di schedulare: il job apre un'altra sessione e deve vedere la riga.
    await session.commit()
    background.add_task(run_market_comparison, task.id, user.id, body.commodity)
    response.headers["Location"] = f"/api/v1/comparisons/{task.id}"
    return ComparisonTaskOut.model_validate(task)


@router.get("/{task_id}", response_model=ComparisonTaskOut)
async def get_comparison(task_id: UUID, repo: TaskRepoDep) -> ComparisonTaskOut:
    task = await repo.get(task_id)
    if task is None:  # 404 anche se esiste ma è di un altro utente: niente enumeration
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task non trovato")
    return ComparisonTaskOut.model_validate(task)
