import pytest
from fastapi import HTTPException

from app.auth.oauth2 import get_current_admin_user
from app.db.models import User
from app.services import auth_service


def test_first_registered_user_becomes_admin(db_session):
    user = auth_service.register_user(db_session, username="first", email="first@example.com", password="testpass123")
    assert user.type == "admin"


def test_second_registered_user_becomes_plain_user(db_session):
    auth_service.register_user(db_session, username="first", email="first@example.com", password="testpass123")
    second = auth_service.register_user(db_session, username="second", email="second@example.com", password="testpass123")
    assert second.type == "user"


def test_create_user_by_admin_honors_requested_type(db_session):
    admin_user = auth_service.create_user_by_admin(
        db_session, username="explicit_admin", email="explicit@example.com",
        password="testpass123", user_type="admin",
    )
    assert admin_user.type == "admin"


def test_get_current_admin_user_allows_admin():
    admin_user = User(id=1, username="admin", email="a@example.com", hashed_password="x", type="admin", is_active=True)
    assert get_current_admin_user(current_user=admin_user) is admin_user


def test_get_current_admin_user_rejects_non_admin():
    plain_user = User(id=2, username="plain", email="p@example.com", hashed_password="x", type="user", is_active=True)
    with pytest.raises(HTTPException) as exc_info:
        get_current_admin_user(current_user=plain_user)
    assert exc_info.value.status_code == 403