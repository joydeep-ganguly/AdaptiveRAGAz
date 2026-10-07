"""
Creates a first user account so your team has something to log in with
right after cloning the repo. Safe to run more than once — if the username
already exists, it just reports that instead of failing.

`python -m app.db.init_db` must have been run first (point 5) so the `users`
table actually exists. Run this from anywhere — sys.path is fixed up below:

    python scripts/seed_admin_user.py

Non-interactive use (e.g. scripting a fresh setup) is supported by setting
ADMIN_USERNAME, ADMIN_EMAIL, and ADMIN_PASSWORD as environment variables
before running — if all three are present, the script skips the prompts.
"""

import getpass
import os
import sys
from pathlib import Path

# scripts/ lives at the project root, one level above backend/ — the `app`
# package lives at backend/app/, so backend/ must be on sys.path before any
# `app.*` import below. Resolved relative to this file, so it works
# regardless of the current working directory the script is run from.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import SessionLocal  # noqa: E402
from app.services import auth_service  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def _collect_credentials() -> tuple[str, str, str]:
    env_username = os.getenv("ADMIN_USERNAME")
    env_email = os.getenv("ADMIN_EMAIL")
    env_password = os.getenv("ADMIN_PASSWORD")

    if env_username and env_email and env_password:
        print("Using ADMIN_USERNAME / ADMIN_EMAIL / ADMIN_PASSWORD from the environment.")
        return env_username, env_email, env_password

    username = input("Username: ").strip()
    email = input("Email: ").strip()

    while True:
        password = getpass.getpass("Password (input hidden): ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            continue
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords didn't match — try again.")
            continue
        break

    return username, email, password


def main() -> None:
    username, email, password = _collect_credentials()

    db = SessionLocal()
    try:
        user = auth_service.register_user(db, username=username, email=email, password=password)
        print(f"Created user '{user.username}' ({user.email}). You can log in with it now.")
    except auth_service.UserAlreadyExistsError as exc:
        print(f"Skipped — {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()