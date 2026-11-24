import os
import secrets
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Locker
from schemas import CommandPollResponse, HeartbeatResponse, StatusResponse
from services.commands import take_command, complete_collection

router = APIRouter(prefix="/api/device")


class DoorReport(BaseModel):
    door_state: Literal["open", "closed"]
    ready: bool = False
    controller_id: UUID | None = None

    @model_validator(mode="after")
    def ready_requires_controller(self):
        if self.ready and self.controller_id is None:
            raise ValueError("A ready controller must have an ID.")
        return self

    model_config = {"extra": "forbid"}


def check_device_key(request: Request):
    key = os.environ.get("UNISTOW_DEVICE_KEY", "")
    if not key:
        raise HTTPException(503, "Device access is not configured.")
    supplied = request.headers.get("Authorization", "")
    if not secrets.compare_digest(supplied.encode(), f"Bearer {key}".encode()):
        raise HTTPException(401, "Invalid device key.")


@router.post("/heartbeat", response_model=HeartbeatResponse, dependencies=[Depends(check_device_key)])
async def heartbeat(report: DoorReport, db: Annotated[AsyncSession, Depends(get_db)]):
    # This prototype key belongs only to the physical Locker 4.
    locker = (await db.execute(
        update(Locker).where(Locker.number == 4).values(
            door_state=report.door_state,
            last_seen_at=func.current_timestamp(),
            device_ready=report.ready,
            controller_id=str(report.controller_id) if report.controller_id else None,
        ).returning(Locker.number)
    )).scalar_one_or_none()
    if locker is None:
        raise HTTPException(404, "Locker 4 has not been configured.")
    await db.commit()
    return {"locker_number": locker, "door_state": report.door_state}


class CommandPoll(BaseModel):
    controller_id: UUID
    supports_finish: bool = False


@router.post("/commands/next", response_model=CommandPollResponse, dependencies=[Depends(check_device_key)])
async def next_command(report: CommandPoll, db: Annotated[AsyncSession, Depends(get_db)]):
    return await take_command(db, str(report.controller_id), report.supports_finish)


class CollectionReport(BaseModel):
    controller_id: UUID


@router.post("/commands/{command_id}/completed", response_model=StatusResponse,
             dependencies=[Depends(check_device_key)])
async def collection_completed(command_id: UUID, report: CollectionReport,
                               db: Annotated[AsyncSession, Depends(get_db)]):
    return await complete_collection(db, str(command_id), str(report.controller_id))
