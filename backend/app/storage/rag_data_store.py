"""
Azure Blob Storage access for the RAG filing PDFs.

Replaces the old local backend/data/rag_data folder. Blobs live in
AZURE_STORAGE_RAG_CONTAINER under AZURE_STORAGE_RAG_DATA_PREFIX (a virtual
"directory" — the account has no hierarchical namespace), one folder per company:

    rag_data/{company}/{company} {doc_type} [{quarter}] {year}.pdf
    e.g. rag_data/amazon/amazon 10-q q1 2024.pdf

This module only STORES and reads files. It does not ingest anything: indexing
the PDFs is done by Azure AI Search (a later sub-plan), not by the app.

The file name is built from the upload form fields (never from the uploaded
file's own name) and follows the same convention the old local ingestion parsed,
so the name stays the source of truth for company / type / year / quarter. The
same four values are also written as blob metadata on upload.

Auth is DefaultAzureCredential: `az login` locally, managed identity in Azure.

Import-time rule: importing this module must not read settings or touch Azure.
tests/conftest.py imports app.main -> admin router -> this module. The client is
therefore built lazily on first use.
"""

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient, ContentSettings

from app.agent.schemas import DocType, FiscalQuarter
from app.config import get_settings

logger = logging.getLogger(__name__)

PDF_EXTENSION = ".pdf"
PDF_CONTENT_TYPE = "application/pdf"
_PDF_MAGIC = b"%PDF-"

MIN_FISCAL_YEAR = 1950
MAX_FISCAL_YEAR = 2050

_COMPANY_NAME_PATTERN = re.compile(r"[a-z0-9\-]+")


# --------------------------------------------------------------------------- #
# Errors — callers (admin router) map these to HTTP codes
# --------------------------------------------------------------------------- #
class RagDataError(Exception):
    """Base class for everything this module raises on purpose."""


class InvalidRagDataFieldsError(RagDataError):
    """Bad file name, company, document type, year, quarter, or not a real PDF."""


class RagDataFileTooLargeError(RagDataError):
    """Over the RAG_DATA_MAX_UPLOAD_MB limit."""


class RagDataFileNotFoundError(RagDataError):
    """No such blob under the rag_data prefix."""


class RagDataStorageError(RagDataError):
    """Azure is misconfigured, unreachable, or rejected the request."""


@dataclass(frozen=True)
class RagDataFile:
    company_name: str
    name: str   # bare file name, e.g. "amazon 10-k 2023.pdf"
    size: int   # bytes


@dataclass(frozen=True)
class RagDocumentFields:
    company_name: str
    doc_type: str
    fiscal_year: int
    fiscal_quarter: str | None


# --------------------------------------------------------------------------- #
# Validation and file-name convention
# --------------------------------------------------------------------------- #
def validate_company_name(company_name: str) -> str:
    """Strips and lower-cases, then accepts only letters, digits and hyphens."""
    company = (company_name or "").strip().lower()
    if not _COMPANY_NAME_PATTERN.fullmatch(company):
        raise InvalidRagDataFieldsError(
            "company_name may only contain lowercase letters, digits, and hyphens"
        )
    return company


def validate_fiscal_year(fiscal_year: int) -> int:
    if (
        isinstance(fiscal_year, bool)
        or not isinstance(fiscal_year, int)
        or not MIN_FISCAL_YEAR <= fiscal_year <= MAX_FISCAL_YEAR
    ):
        raise InvalidRagDataFieldsError(
            f"fiscal_year must be between {MIN_FISCAL_YEAR} and {MAX_FISCAL_YEAR}"
        )
    return fiscal_year


def validate_upload_filename(name: str) -> str:
    """The uploaded file's own name is only checked for the .pdf extension."""
    if os.path.splitext(name or "")[1].lower() != PDF_EXTENSION:
        raise InvalidRagDataFieldsError("Only .pdf files are allowed")
    return name


def _coerce_enum(enum_cls, value, label: str):
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        raise InvalidRagDataFieldsError(f"{label} must be one of: {allowed}") from None


def build_filename(
    company_name: str,
    doc_type: "DocType | str",
    fiscal_year: int,
    fiscal_quarter: "FiscalQuarter | str | None" = None,
) -> str:
    """'{company} {doc_type} [{quarter}] {year}.pdf', validating every part."""
    parts = [
        validate_company_name(company_name),
        _coerce_enum(DocType, doc_type, "doc_type").value,
    ]
    if fiscal_quarter is not None:
        parts.append(_coerce_enum(FiscalQuarter, fiscal_quarter, "fiscal_quarter").value)
    parts.append(str(validate_fiscal_year(fiscal_year)))
    return " ".join(parts) + PDF_EXTENSION


def parse_filename(name: str) -> RagDocumentFields:
    """
    Inverse of build_filename. Strict: the name must be exactly what
    build_filename would produce for the values it contains.
    """
    expected_shape = "'{company} {doc_type} [{quarter}] {year}.pdf'"
    base, extension = os.path.splitext(name or "")
    parts = base.split(" ")

    if extension != PDF_EXTENSION or len(parts) not in (3, 4):
        raise InvalidRagDataFieldsError(f"'{name}' does not match {expected_shape}")

    quarter = parts[2] if len(parts) == 4 else None
    company, doc_type, year_text = parts[0], parts[1], parts[-1]

    try:
        year = int(year_text)
        rebuilt = build_filename(company, doc_type, year, quarter)
    except (ValueError, InvalidRagDataFieldsError):
        raise InvalidRagDataFieldsError(f"'{name}' does not match {expected_shape}") from None

    if rebuilt != name:
        raise InvalidRagDataFieldsError(f"'{name}' does not match {expected_shape}")

    return RagDocumentFields(
        company_name=company, doc_type=doc_type, fiscal_year=year, fiscal_quarter=quarter
    )


def _validate_bare_pdf_name(name: str) -> str:
    if (
        not name
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or name in (".", "..")
        or os.path.splitext(name)[1].lower() != PDF_EXTENSION
    ):
        raise InvalidRagDataFieldsError("Invalid filename")
    return name


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _prefix() -> str:
    prefix = get_settings().azure_storage_rag_data_prefix.strip("/")
    return f"{prefix}/" if prefix else ""


def _blob_name(company_name: str, name: str) -> str:
    return f"{_prefix()}{company_name}/{name}"


def _max_mb() -> int:
    return get_settings().rag_data_max_upload_mb


def _max_bytes() -> int:
    return _max_mb() * 1024 * 1024


def _storage_error(action: str, exc: Exception) -> RagDataStorageError:
    # Full Azure detail goes to the log only; the message callers see stays short.
    logger.warning("Azure Blob Storage error while %s: %s", action, exc, exc_info=True)
    return RagDataStorageError(
        f"Azure Blob Storage request failed while {action} ({type(exc).__name__})"
    )


def _measure(data: "bytes | BinaryIO") -> int:
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    data.seek(0, os.SEEK_END)
    size = data.tell()
    data.seek(0)
    return size


def _has_pdf_header(data: "bytes | BinaryIO") -> bool:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data[: len(_PDF_MAGIC)]) == _PDF_MAGIC
    data.seek(0)
    head = data.read(len(_PDF_MAGIC))
    data.seek(0)
    return head == _PDF_MAGIC


@lru_cache
def _get_container_client() -> ContainerClient:
    """Built once per process, on first use (never at import). Thread-safe client."""
    settings = get_settings()
    if not settings.azure_storage_account_name:
        raise RagDataStorageError("AZURE_STORAGE_ACCOUNT_NAME is not configured")

    account_url = f"https://{settings.azure_storage_account_name}.blob.core.windows.net"
    service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
    return service.get_container_client(settings.azure_storage_rag_container)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def list_files(company_name: str | None = None) -> list[RagDataFile]:
    """
    PDFs directly inside a company folder under the rag_data prefix, sorted by
    company then name. Folder placeholders, nested blobs, blobs outside a company
    folder and non-PDFs are skipped. File names are not checked against the
    naming convention here (use parse_filename for that).
    """
    prefix = _prefix()
    start = prefix
    if company_name is not None:
        start = f"{prefix}{validate_company_name(company_name)}/"

    container = _get_container_client()

    files: list[RagDataFile] = []
    try:
        for blob in container.list_blobs(name_starts_with=start):
            company, separator, name = blob.name[len(prefix):].partition("/")
            if (
                not separator
                or "/" in name
                or not _COMPANY_NAME_PATTERN.fullmatch(company)
                or os.path.splitext(name)[1].lower() != PDF_EXTENSION
            ):
                continue
            files.append(RagDataFile(company_name=company, name=name, size=blob.size))
    except AzureError as exc:
        raise _storage_error("listing files", exc) from exc

    return sorted(files, key=lambda f: (f.company_name, f.name.lower()))


def download(company_name: str, name: str) -> bytes:
    """Whole file as bytes. Refuses files over the size limit rather than loading them."""
    company = validate_company_name(company_name)
    _validate_bare_pdf_name(name)
    container = _get_container_client()

    try:
        downloader = container.download_blob(_blob_name(company, name))
        if downloader.size > _max_bytes():
            raise RagDataFileTooLargeError(f"'{name}' is larger than the {_max_mb()} MB limit")
        return downloader.readall()
    except ResourceNotFoundError as exc:
        raise RagDataFileNotFoundError(f"'{name}' was not found in RAG data storage") from exc
    except AzureError as exc:
        raise _storage_error(f"downloading '{name}'", exc) from exc


def upload(
    original_filename: str,
    company_name: str,
    doc_type: "DocType | str",
    fiscal_year: int,
    fiscal_quarter: "FiscalQuarter | str | None",
    data: "bytes | BinaryIO",
) -> RagDataFile:
    """
    Stores a PDF at rag_data/{company}/{constructed file name}. An existing blob
    with the same name is silently overwritten. `data` is bytes or a seekable
    binary stream (e.g. FastAPI's UploadFile.file). Every check runs before
    anything is sent: .pdf extension of the original name, company / type / year /
    quarter, size limit, and the %PDF- header. Company, type, year and quarter
    are also written as blob metadata (quarter omitted for annual reports).
    """
    validate_upload_filename(original_filename)
    name = build_filename(company_name, doc_type, fiscal_year, fiscal_quarter)
    fields = parse_filename(name)

    size = _measure(data)
    if size > _max_bytes():
        raise RagDataFileTooLargeError(f"File is larger than the {_max_mb()} MB limit")
    if not _has_pdf_header(data):
        raise InvalidRagDataFieldsError("File is not a valid PDF")

    metadata = {
        "company_name": fields.company_name,
        "doc_type": fields.doc_type,
        "fiscal_year": str(fields.fiscal_year),
    }
    if fields.fiscal_quarter:
        metadata["fiscal_quarter"] = fields.fiscal_quarter

    container = _get_container_client()
    try:
        container.upload_blob(
            name=_blob_name(fields.company_name, name),
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=PDF_CONTENT_TYPE),
            metadata=metadata,
        )
    except AzureError as exc:
        raise _storage_error(f"uploading '{name}'", exc) from exc

    return RagDataFile(company_name=fields.company_name, name=name, size=size)
