from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.models.models import User


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------
# 🔐 PASSWORDS
# ---------------------------

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------
# 🔑 TOKENS
# ---------------------------

def _create_token(user_id: str, token_type: str, expires_in: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_in
    payload = {"sub": user_id, "exp": expire, "typ": token_type}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str) -> str:
    return _create_token(
        user_id=user_id,
        token_type="access",
        expires_in=timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    )


def create_guest_token(user_id: str) -> str:
    return _create_token(
        user_id=user_id,
        token_type="guest",
        expires_in=timedelta(hours=settings.GUEST_JWT_EXPIRE_HOURS),
    )


# ---------------------------
# ⚠️ EXCEPTIONS
# ---------------------------

def _credentials_exception(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_token(token: str, expected_type: str) -> str:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("typ")

        if not user_id or token_type != expected_type:
            raise _credentials_exception()

        return user_id

    except JWTError as exc:
        raise _credentials_exception() from exc


# ---------------------------
# 🗄️ DB HELPERS
# ---------------------------

async def _load_user_or_401(db: AsyncSession, user_id: str) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise _credentials_exception()
    return user


# ---------------------------
# 👤 OPTIONAL USER
# ---------------------------

async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User | None:
    if not credentials:
        return None

    if credentials.scheme.lower() != "bearer":
        raise _credentials_exception("Invalid authentication scheme")

    user_id = _decode_token(credentials.credentials, expected_type="access")
    return await _load_user_or_401(db, user_id)


async def get_current_user(
    user: User | None = Depends(get_optional_user),
) -> User:
    if not user:
        raise _credentials_exception()
    return user


# ---------------------------
# 🧠 IDENTITY CONTEXT
# ---------------------------

@dataclass
class IdentityContext:
    user: User
    is_guest: bool
    guest_token: str | None = None


# ---------------------------
# 🔥 MAIN AUTH DEPENDENCY
# ---------------------------

async def get_current_or_guest_user(
    request: Request,
    access_credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db: AsyncSession = Depends(get_session),
) -> IdentityContext:

    user: User | None = None
    is_guest = False
    guest_token: str | None = None

    # ---------------------------
    # ✅ ACCESS TOKEN (PRIORITY)
    # ---------------------------
    if access_credentials:
        if access_credentials.scheme.lower() != "bearer":
            raise _credentials_exception("Invalid authentication scheme")

        user_id = _decode_token(access_credentials.credentials, expected_type="access")
        user = await _load_user_or_401(db, user_id)

    # ---------------------------
    # 🟡 EXISTING GUEST TOKEN
    # ---------------------------
    elif x_guest_token:
        user_id = _decode_token(x_guest_token, expected_type="guest")
        user = await _load_user_or_401(db, user_id)
        is_guest = True
        guest_token = x_guest_token

    # ---------------------------
    # 🆕 CREATE NEW GUEST
    # ---------------------------
    else:
        user = User(account_type="home")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        is_guest = True
        guest_token = create_guest_token(user.id)

    # ---------------------------
    # 🔥 CRITICAL (RATE LIMITING)
    # ---------------------------
    request.state.user = user

    return IdentityContext(
        user=user,
        is_guest=is_guest,
        guest_token=guest_token,
    )