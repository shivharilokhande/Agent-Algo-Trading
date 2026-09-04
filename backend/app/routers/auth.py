"""F1 — accounts and sessions."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from ..security import (
    check_rate_limit,
    create_access_token,
    dummy_verify,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_key(request: Request, email: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"auth:{ip}:{email.lower()}"


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    check_rate_limit(_client_key(request, body.email), limit=5)  # S6
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    check_rate_limit(_client_key(request, body.email), limit=10)  # S6
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user is None:
        dummy_verify()  # S6: equalize timing so email existence isn't observable
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: Annotated[User, Depends(get_current_user)]):
    return UserOut(id=user.id, email=user.email, is_admin=user.is_admin, created_at=user.created_at)


@router.delete("/me", status_code=204)
async def delete_account(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """F1.3 — account deletion cascades ALL user data (C1: explicit dependents)."""
    from ..models import MemoryEntry, Preset, Setting
    from ..runner import cancel_all_for_user

    await cancel_all_for_user(user.id)  # M5: no orphaned engine subprocesses

    db.query(MemoryEntry).filter(MemoryEntry.user_id == user.id).delete()
    db.query(Preset).filter(Preset.user_id == user.id).delete()
    db.query(Setting).filter(Setting.user_id == user.id).delete()
    db.delete(db.get(User, user.id))  # runs/events/reports/keys via ORM cascades
    db.commit()
