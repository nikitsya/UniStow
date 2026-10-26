import os
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db


router = APIRouter(prefix="/api/device")


class DoorReport(BaseModel):
    door_state: Literal["open", "closed"]
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
        SET door_state = :state, last_seen_at = CURRENT_TIMESTAMP
        WHERE number = 4
        RETURNING number
    """), {"state": report.door_state}).scalar_one_or_none()
    if locker is None:
        raise HTTPException(404, "Locker 4 has not been configured.")
    db.commit()
    return {"locker_number": locker, "door_state": report.door_state}
