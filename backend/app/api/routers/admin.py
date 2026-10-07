import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.agent.retrieval.ingestion import ingest_docs_in_vectordb
from app.agent.schemas import DocType, FiscalQuarter
from app.api.schemas.admin import AdminCreateUserRequest, FileUploadResponse, IngestionResponse
from app.api.schemas.auth import UserResponse
from app.config import get_settings
from app.db.models import User
from app.deps import get_current_admin_user, get_db
from app.services import auth_service
from app.storage import code_data_store

router = APIRouter(prefix="/admin", tags=["admin"])

_COMPANY_NAME_PATTERN = re.compile(r"^[a-z0-9\-]+$")


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminCreateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    try:
        user = auth_service.create_user_by_admin(
            db, username=payload.username, email=payload.email,
            password=payload.password, user_type=payload.type,
        )
    except auth_service.UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return user


@router.post("/uploads/code-data", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_code_data_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Admin-only. Uploads a .csv/.xlsx to Azure Blob Storage (code_data/) for the
    code-execution agent to analyze. An existing file with the same name is
    overwritten. Filename and size rules live in code_data_store.
    """
    try:
        code_data_store.upload(file.filename or "", file.file.read())
    except code_data_store.InvalidCodeDataFilenameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except code_data_store.CodeDataFileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc))
    except code_data_store.CodeDataStorageError:
        # Azure detail is already logged by code_data_store; don't leak it to the client.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not store the file in Azure Blob Storage. Please try again later.",
        )

    return FileUploadResponse(filename=file.filename, message="File uploaded successfully.")

@router.post("/uploads/rag-data", response_model=IngestionResponse, status_code=status.HTTP_201_CREATED)
def upload_rag_data_file(
    file: UploadFile = File(...),
    company_name: str = Form(...),
    doc_type: DocType = Form(...),
    fiscal_year: int = Form(...),
    fiscal_quarter: Optional[FiscalQuarter] = Form(None),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Admin-only. Saves a PDF into rag_data/{company_name}/ using a filename
    constructed from the form fields (not the uploaded file's own name) so
    ingestion.extract_metadata_from_filename always succeeds, then triggers
    ingestion into the shared Chroma index for that one file.

    Deliberately `def`, not `async def` — docling's PDF conversion plus
    embedding every page is genuinely slow, and this keeps that blocking
    work in FastAPI's thread pool instead of freezing the server for
    everyone else (same rule as /chat/{conversation_id}/query, section 4
    of the original plan, bug #4).
    """
    settings = get_settings()

    if os.path.splitext(file.filename or "")[1].lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .pdf files are allowed")

    company_name = company_name.strip().lower()
    if not _COMPANY_NAME_PATTERN.fullmatch(company_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="company_name may only contain lowercase letters, digits, and hyphens",
        )

    name_parts = [company_name, doc_type.value]
    if fiscal_quarter is not None:
        name_parts.append(fiscal_quarter.value)
    name_parts.append(str(fiscal_year))
    constructed_filename = " ".join(name_parts) + ".pdf"

    company_dir = settings.resolved_rag_data_dir / company_name
    company_dir.mkdir(parents=True, exist_ok=True)
    destination = company_dir / constructed_filename

    with open(destination, "wb") as f:
        f.write(file.file.read())

    result = ingest_docs_in_vectordb(destination)

    return IngestionResponse(
        filename=constructed_filename,
        pages_ingested=result["pages_ingested"],
        skipped=result["skipped"],
    )