import httpx

from config import API_BASE_URL

DEFAULT_TIMEOUT = 30


class ApiError(Exception):
    """Raised for both HTTP error responses and network-level failures (e.g. backend not running)."""
    def __init__(self, status_code, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _request(method: str, path: str, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> httpx.Response:
    url = f"{API_BASE_URL}{path}"
    try:
        response = httpx.request(method, url, timeout=timeout, **kwargs)
    except httpx.RequestError as exc:
        raise ApiError(None, f"Could not reach the backend at {API_BASE_URL} ({exc})")

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise ApiError(response.status_code, detail)

    return response


def register(username: str, email: str, password: str) -> dict:
    return _request(
        "POST", "/auth/register",
        json={"username": username, "email": email, "password": password},
    ).json()


def login(username: str, password: str) -> str:
    response = _request("POST", "/auth/token", data={"username": username, "password": password})
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def get_me(token: str) -> dict:
    return _request("GET", "/auth/me", headers=_auth_headers(token)).json()


def create_conversation(token: str, title: str = "New conversation") -> dict:
    return _request(
        "POST", "/conversations",
        json={"title": title}, headers=_auth_headers(token),
    ).json()


def list_conversations(token: str) -> list[dict]:
    return _request("GET", "/conversations", headers=_auth_headers(token)).json()


def get_messages(token: str, conversation_id: str) -> list[dict]:
    return _request(
        "GET", f"/conversations/{conversation_id}/messages",
        headers=_auth_headers(token),
    ).json()


def delete_conversation(token: str, conversation_id: str) -> None:
    _request("DELETE", f"/conversations/{conversation_id}", headers=_auth_headers(token))


def send_message(token: str, conversation_id: str, message: str) -> dict:
    # Agent turns (especially sql_agent / code_execution) can run several tool
    # calls, so this one gets a longer timeout than the plain CRUD calls above.
    return _request(
        "POST", f"/chat/{conversation_id}/query",
        json={"message": message}, headers=_auth_headers(token), timeout=120,
    ).json()


def get_media_bytes(token: str, filename: str) -> bytes:
    """Fetches a generated chart image. Not yet called automatically from
    chat_window.py — see the explanation above for why."""
    return _request("GET", f"/media/{filename}", headers=_auth_headers(token)).content


def create_user_as_admin(token: str, username: str, email: str, password: str, user_type: str) -> dict:
    return _request(
        "POST", "/admin/users",
        json={"username": username, "email": email, "password": password, "type": user_type},
        headers=_auth_headers(token),
    ).json()


def upload_code_data_file(token: str, file_bytes: bytes, filename: str) -> dict:
    content_type = (
        "text/csv" if filename.lower().endswith(".csv")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return _request(
        "POST", "/admin/uploads/code-data",
        headers=_auth_headers(token),
        files={"file": (filename, file_bytes, content_type)},
        timeout=60,
    ).json()


def upload_rag_data_file(
    token: str, file_bytes: bytes, filename: str,
    company_name: str, doc_type: str, fiscal_year: int, fiscal_quarter: str | None = None,
) -> dict:
    data = {
        "company_name": company_name,
        "doc_type": doc_type,
        "fiscal_year": str(fiscal_year),
    }
    if fiscal_quarter:
        data["fiscal_quarter"] = fiscal_quarter

    return _request(
        "POST", "/admin/uploads/rag-data",
        headers=_auth_headers(token),
        files={"file": (filename, file_bytes, "application/pdf")},
        data=data,
        timeout=180,  # docling conversion + per-page embeddings can take a while for a large filing
    ).json()