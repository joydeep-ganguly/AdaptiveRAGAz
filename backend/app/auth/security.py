from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password[:72])


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password[:72], hashed_password)


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    """
    `subject` is whatever uniquely identifies the user in the token — the
    username, per this project's design (see get_current_user in oauth2.py).
    """
    settings = get_settings()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[str]:
    """Returns the `sub` claim (username) if the token is valid, else None."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None