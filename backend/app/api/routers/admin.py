from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.agent.schemas import DocType, FiscalQuarter
from app.api.schemas.admin import AdminCreateUserRequest, FileUploadResponse
from app.api.schemas.auth import UserResponse
from app.db.models import User
from app.deps import get_current_admin_user, get_db
from app.services import auth_service
from app.storage import code_data_store, rag_data_store

router = APIRouter(prefix="/admin", tags=["admin"])


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

@router.post("/uploads/rag-data", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_rag_data_file(
    file: UploadFile = File(...),
    company_name: str = Form(...),
    doc_type: DocType = Form(...),
    fiscal_year: int = Form(...),
    fiscal_quarter: Optional[FiscalQuarter] = Form(None),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Admin-only. Uploads a filing PDF to Azure Blob Storage (rag_data/{company}/)
    under a filename constructed from the form fields (not the uploaded file's
    own name), so the name always follows "{company} {doc_type} [{quarter}] {year}.pdf".
    An existing file with the same name is overwritten. Validation rules live in
    rag_data_store.

    This only STORES the file. Nothing is ingested here: the Azure AI Search
    indexer picks up the new blob on its next scheduled run, after which the
    PDF is searchable in chat.
    """
    try:
        stored = rag_data_store.upload(
            file.filename or "",
            company_name,
            doc_type,
            fiscal_year,
            fiscal_quarter,
            file.file.read(),
        )
    except rag_data_store.InvalidRagDataFieldsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except rag_data_store.RagDataFileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc))
    except rag_data_store.RagDataStorageError:
        # Azure detail is already logged by rag_data_store; don't leak it to the client.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not store the file in Azure Blob Storage. Please try again later.",
        )

    return FileUploadResponse(filename=stored.name, message="File uploaded successfully.")
