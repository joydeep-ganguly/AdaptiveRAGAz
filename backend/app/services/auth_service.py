from sqlalchemy.orm import Session

from app.auth.security import create_access_token, hash_password, verify_password
from app.db import crud
from app.db.models import User


class UserAlreadyExistsError(Exception):
    """Raised when registering a username or email that's already taken."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials don't match any active user."""


def _create_user_common(db: Session, username: str, email: str, password: str, user_type: str) -> User:
    """Shared uniqueness checks + hashing for both the public and admin-driven creation paths."""
    if crud.get_user_by_username(db, username) is not None:
        raise UserAlreadyExistsError(f"Username '{username}' is already taken")
    if crud.get_user_by_email(db, email) is not None:
        raise UserAlreadyExistsError(f"Email '{email}' is already registered")

    hashed_password = hash_password(password)
    return crud.create_user(db, username=username, email=email, hashed_password=hashed_password, user_type=user_type)


def register_user(db: Session, username: str, email: str, password: str) -> User:
    """
    Public, unauthenticated self-registration (POST /auth/register). Never
    accepts a `type` from the caller — the only way to become admin through
    this path is being the very first user ever registered. Every
    registration after that is forced to "user", regardless of what anyone
    requests, since honoring a caller-supplied type here would be a
    privilege-escalation hole.
    """
    user_type = "admin" if crud.count_users(db) == 0 else "user"
    return _create_user_common(db, username, email, password, user_type)


def create_user_by_admin(db: Session, username: str, email: str, password: str, user_type: str) -> User:
    """
    Admin-driven creation (POST /admin/users, point #10) — the only place a
    caller-supplied `type` is ever honored. The router is responsible for
    confirming the caller is already an admin (via get_current_admin_user)
    before this is ever called.
    """
    return _create_user_common(db, username, email, password, user_type)


def authenticate_user(db: Session, username: str, password: str) -> User:
    user = crud.get_user_by_username(db, username)
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError("Incorrect username or password")
    if not user.is_active:
        raise InvalidCredentialsError("This account is inactive")
    return user


def login_user(db: Session, username: str, password: str) -> str:
    """Returns a signed access token for a valid username/password pair."""
    user = authenticate_user(db, username, password)
    return create_access_token(subject=user.username)