from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.db import crud
from app.db.models import User
from app.db.session import get_db_session

# tokenUrl points at the endpoint point 8 will add: POST /auth/token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db_session),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    username = decode_access_token(token)
    if username is None:
        raise credentials_exception

    user = crud.get_user_by_username(db, username)
    if user is None:
        raise credentials_exception

    return user


def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")
    return current_user


def get_current_admin_user(current_user: User = Depends(get_current_active_user)) -> User:
    if current_user.type != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user