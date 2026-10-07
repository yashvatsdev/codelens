from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
import re
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

from app.core.config import settings

ph = PasswordHasher()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return ph.verify(hashed_password, plain_password)
    except (VerificationError, InvalidHashError):
        return False

def get_password_hash(password: str) -> str:
    return ph.hash(password)

def create_access_token(subject: str | int, expires_delta: timedelta | None = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.auth_token_expire_minutes
        )
    to_encode = {"exp": expire, "sub": str(subject), "jti": secrets.token_urlsafe(24)}
    encoded_jwt = jwt.encode(to_encode, settings.auth_secret_key, algorithm="HS256")
    return encoded_jwt


def decode_session_user_id(token: str) -> int:
    """Verify the session before trusting identity, including in middleware."""
    payload = jwt.decode(token, settings.auth_secret_key, algorithms=["HS256"],
                         options={"require": ["exp", "sub"]})
    sub = payload["sub"]
    if not isinstance(sub, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", sub):
        raise jwt.InvalidTokenError("Invalid subject")
    user_id = int(sub)
    if user_id > 2_147_483_647:
        raise jwt.InvalidTokenError("Invalid subject")
    return user_id
