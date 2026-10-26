import os
import secrets
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from commands import take_command


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
    key = os.environ.get("PAYBOX_DEVICE_KEY", "")
    if not key:
        raise HTTPException(503, "Device access is not configured.")
    supplied = request.headers.get("Authorization", "")
    if not secrets.compare_digest(supplied.encode(), f"Bearer {key}".encode()):
        raise HTTPException(401, "Invalid device key.")


@router.post("/heartbeat", dependencies=[Depends(check_device_key)])
def heartbeat(report: DoorReport, db: Annotated[Session, Depends(get_db)]):
    # This prototype key belongs only to the physical Locker 4.
    locker = db.execute(text("""
        UPDATE lockers
        SET door_state = :state, last_seen_at = CURRENT_TIMESTAMP,
            device_ready = :ready, controller_id = :controller
        WHERE number = 4
        RETURNING number
    """), {"state": report.door_state, "ready": report.ready,
          "controller": str(report.controller_id) if report.controller_id else None}).scalar_one_or_none()
    if locker is None:
        raise HTTPException(404, "Locker 4 has not been configured.")
    db.commit()
    return {"locker_number": locker, "door_state": report.door_state}


class CommandPoll(BaseModel):
    controller_id: UUID


@router.post("/commands/next", dependencies=[Depends(check_device_key)])
def next_command(report: CommandPoll, db: Annotated[Session, Depends(get_db)]):
    return take_command(db, str(report.controller_id))
