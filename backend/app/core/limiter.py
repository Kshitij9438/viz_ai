from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse


def user_or_ip_key(request: Request) -> str:
    user = getattr(request.state, "user", None)

    if user:
        key = f"user:{user.id}"
    else:
        key = f"ip:{get_remote_address(request)}"

    # 🔥 DEBUG LINE
    print("RATE LIMIT KEY:", key)

    return key

limiter = Limiter(key_func=user_or_ip_key)


def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please slow down.",
        },
    )