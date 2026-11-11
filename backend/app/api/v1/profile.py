"""Profilo dell'utente: per ora solo la regione di fornitura, usata per filtrare le offerte locali."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select, update

from app.core.security import CurrentUserDep
from app.db.models import User
from app.db.repositories import TenantRepository
from app.db.session import SessionDep
from app.market.codes import REGIONS

router = APIRouter(prefix="/me", tags=["profile"])


class ProfileOut(BaseModel):
    email: str | None
    region_code: str | None
    region_name: str | None


class ProfileUpdate(BaseModel):
    region_code: str

    @field_validator("region_code")
    @classmethod
    def _known_region(cls, value: str) -> str:
        if value not in REGIONS:
            raise ValueError("Regione non valida")
        return value


@router.get("", response_model=ProfileOut)
async def get_profile(user: CurrentUserDep, session: SessionDep) -> ProfileOut:
    await TenantRepository(session, user.id).ensure_user(user.email)
    await session.commit()
    region = await session.scalar(select(User.region_code).where(User.id == user.id))
    return ProfileOut(email=user.email, region_code=region, region_name=REGIONS.get(region or ""))


@router.put("", response_model=ProfileOut)
async def update_profile(body: ProfileUpdate, user: CurrentUserDep, session: SessionDep) -> ProfileOut:
    await TenantRepository(session, user.id).ensure_user(user.email)
    result = await session.execute(update(User).where(User.id == user.id).values(region_code=body.region_code))
    if not result.rowcount:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utente non trovato")
    await session.commit()
    return ProfileOut(email=user.email, region_code=body.region_code, region_name=REGIONS[body.region_code])
