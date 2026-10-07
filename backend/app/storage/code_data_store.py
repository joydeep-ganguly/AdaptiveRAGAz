"""
Azure Blob Storage access for the code-execution datasets.

Replaces the old local backend/data/code_data folder. Blobs live in
AZURE_STORAGE_CONTAINER under AZURE_STORAGE_CODE_DATA_PREFIX (a virtual
"directory" — the account has no hierarchical namespace). Callers always use
bare file names such as "titanic.csv"; the prefix is added and removed here.

Auth is DefaultAzureCredential: `az login` locally, managed identity in Azure.

Import-time rule: importing this module must not read settings or touch Azure.
tests/conftest.py imports app.main -> agent graph -> code_tools, which will
import this module. The client is therefore built lazily on first use.
"""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient, ContentSettings

from app.config import get_settings

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (".csv", ".xlsx")

_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# --------------------------------------------------------------------------- #
# Errors — callers (admin router, agent tools) map these to HTTP codes / messages
# --------------------------------------------------------------------------- #
class CodeDataError(Exception):
    """Base class for everything this module raises on purpose."""


class InvalidCodeDataFilenameError(CodeDataError):
    """Not a bare .csv/.xlsx filename."""


class CodeDataFileTooLargeError(CodeDataError):
    """Over the CODE_DATA_MAX_UPLOAD_MB limit."""


class CodeDataFileNotFoundError(CodeDataError):
    """No such blob under the code_data prefix."""


class CodeDataStorageError(CodeDataError):
    """Azure is misconfigured, unreachable, or rejected the request."""


@dataclass(frozen=True)
class CodeDataFile:
    name: str   # bare file name, without the blob prefix
    size: int   # bytes


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def validate_filename(name: str) -> str:
    """Accepts only a bare .csv/.xlsx filename. Messages match the old admin endpoint."""
    if (
        not name
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or name in (".", "..")
    ):
        raise InvalidCodeDataFilenameError("Invalid filename")

    if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
        raise InvalidCodeDataFilenameError("Only .csv and .xlsx files are allowed")

    return name


def _prefix() -> str:
    prefix = get_settings().azure_storage_code_data_prefix.strip("/")
    return f"{prefix}/" if prefix else ""


def _blob_name(name: str) -> str:
    return f"{_prefix()}{name}"


def _max_mb() -> int:
    return get_settings().code_data_max_upload_mb


def _max_bytes() -> int:
    return _max_mb() * 1024 * 1024


def _storage_error(action: str, exc: Exception) -> CodeDataStorageError:
    # Full Azure detail goes to the log only; the message callers see stays short.
    logger.warning("Azure Blob Storage error while %s: %s", action, exc, exc_info=True)
    return CodeDataStorageError(
        f"Azure Blob Storage request failed while {action} ({type(exc).__name__})"
    )


def _measure(data: "bytes | BinaryIO") -> int:
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    data.seek(0, os.SEEK_END)
    size = data.tell()
    data.seek(0)
    return size


@lru_cache
def _get_container_client() -> ContainerClient:
    """Built once per process, on first use (never at import). Thread-safe client."""
    settings = get_settings()
    if not settings.azure_storage_account_name:
        raise CodeDataStorageError("AZURE_STORAGE_ACCOUNT_NAME is not configured")

    account_url = f"https://{settings.azure_storage_account_name}.blob.core.windows.net"
    service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
    return service.get_container_client(settings.azure_storage_container)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def list_files() -> list[CodeDataFile]:
    """
    Dataset files under the code_data prefix, sorted by name. Folder placeholders,
    nested blobs, and anything that isn't .csv/.xlsx are skipped, so the list only
    contains files that download() / upload() would accept.
    """
    prefix = _prefix()
    container = _get_container_client()

    files: list[CodeDataFile] = []
    try:
        for blob in container.list_blobs(name_starts_with=prefix):
            name = blob.name[len(prefix):]
            try:
                validate_filename(name)
            except InvalidCodeDataFilenameError:
                continue
            files.append(CodeDataFile(name=name, size=blob.size))
    except AzureError as exc:
        raise _storage_error("listing files", exc) from exc

    return sorted(files, key=lambda f: f.name.lower())


def download(name: str) -> bytes:
    """Whole file as bytes. Refuses files over the size limit rather than loading them."""
    validate_filename(name)
    container = _get_container_client()

    try:
        downloader = container.download_blob(_blob_name(name))
        if downloader.size > _max_bytes():
            raise CodeDataFileTooLargeError(
                f"'{name}' is larger than the {_max_mb()} MB limit"
            )
        return downloader.readall()
    except ResourceNotFoundError as exc:
        raise CodeDataFileNotFoundError(f"'{name}' was not found in code data storage") from exc
    except AzureError as exc:
        raise _storage_error(f"downloading '{name}'", exc) from exc


def upload(name: str, data: "bytes | BinaryIO") -> None:
    """
    Stores a file under the code_data prefix. An existing blob with the same
    name is silently overwritten (decision 2). `data` is bytes or a seekable
    binary stream (e.g. FastAPI's UploadFile.file); the size limit is checked
    before anything is sent.
    """
    validate_filename(name)

    if _measure(data) > _max_bytes():
        raise CodeDataFileTooLargeError(f"File is larger than the {_max_mb()} MB limit")

    content_type = _CONTENT_TYPES[os.path.splitext(name)[1].lower()]
    container = _get_container_client()

    try:
        container.upload_blob(
            name=_blob_name(name),
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
    except AzureError as exc:
        raise _storage_error(f"uploading '{name}'", exc) from exc