from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, create_guest_token, hash_password, verify_password
from app.core.db import get_session
from app.models.models import User, UserCredential

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/guest")
async def create_guest(db: AsyncSession = Depends(get_session)) -> dict:
    user = User(account_type="home")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_guest_token(user.id)
    return {"guest_token": token, "user_id": user.id}


@router.post("/register")
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_session)) -> dict:
    existing = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(email=body.email, name=body.name, account_type="home")
    try:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered") from exc

    cred = UserCredential(user_id=user.id, hashed_password=hash_password(body.password))
    db.add(cred)
    await db.commit()

    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer", "user_id": user.id}


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_session)) -> dict:
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    cred = (await db.execute(select(UserCredential).where(UserCredential.user_id == user.id))).scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(body.password, cred.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.last_active = datetime.utcnow()
    await db.commit()

    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer", "user_id": user.id}
