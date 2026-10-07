import pytest
from azure.core.exceptions import AzureError


def test_create_user_requires_admin(client, non_admin_auth_headers):
    response = client.post(
        "/admin/users",
        json={"username": "newbie", "email": "newbie@example.com", "password": "testpass123", "type": "user"},
        headers=non_admin_auth_headers,
    )
    assert response.status_code == 403


def test_upload_code_data_requires_admin(client, non_admin_auth_headers):
    response = client.post(
        "/admin/uploads/code-data",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        headers=non_admin_auth_headers,
    )
    assert response.status_code == 403


def test_upload_rag_data_requires_admin(client, non_admin_auth_headers):
    response = client.post(
        "/admin/uploads/rag-data",
        files={"file": ("filing.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"company_name": "amazon", "doc_type": "10-k", "fiscal_year": "2024"},
        headers=non_admin_auth_headers,
    )
    assert response.status_code == 403


def test_admin_can_create_user(client, auth_headers):
    response = client.post(
        "/admin/users",
        json={"username": "newbie", "email": "newbie@example.com", "password": "testpass123", "type": "admin"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "newbie"
    assert body["type"] == "admin"


def test_admin_upload_code_data_happy_path(client, auth_headers, fake_code_data_blob):
    response = client.post(
        "/admin/uploads/code-data",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json() == {"filename": "data.csv", "message": "File uploaded successfully."}
    assert fake_code_data_blob.blobs["code_data/data.csv"] == b"a,b\n1,2\n"
    upload = fake_code_data_blob.uploads[0]
    assert upload.overwrite is True
    assert upload.content_type == "text/csv"


def test_admin_upload_code_data_xlsx_content_type(client, auth_headers, fake_code_data_blob):
    response = client.post(
        "/admin/uploads/code-data",
        files={"file": ("sheet.xlsx", b"PK-fake", "application/octet-stream")},
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert fake_code_data_blob.uploads[0].content_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_admin_upload_code_data_overwrites_existing_file(client, auth_headers, fake_code_data_blob):
    for content in (b"first", b"second"):
        response = client.post(
            "/admin/uploads/code-data",
            files={"file": ("data.csv", content, "text/csv")},
            headers=auth_headers,
        )
        assert response.status_code == 201

    assert fake_code_data_blob.blobs["code_data/data.csv"] == b"second"


@pytest.mark.parametrize("filename", ["notes.txt", "report.pdf"])
def test_admin_upload_code_data_rejects_other_extensions(client, auth_headers, fake_code_data_blob, filename):
    response = client.post(
        "/admin/uploads/code-data",
        files={"file": (filename, b"hello", "application/octet-stream")},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only .csv and .xlsx files are allowed"
    assert fake_code_data_blob.uploads == []


def test_admin_upload_code_data_rejects_oversized_file(client, auth_headers, fake_code_data_blob):
    too_big = b"x" * (1024 * 1024 + 1)  # fixture limit is 1 MB
    response = client.post(
        "/admin/uploads/code-data",
        files={"file": ("big.csv", too_big, "text/csv")},
        headers=auth_headers,
    )

    assert response.status_code == 413
    assert fake_code_data_blob.uploads == []


def test_admin_upload_code_data_storage_failure_returns_502(client, auth_headers, fake_code_data_blob):
    fake_code_data_blob.error = AzureError("boom: secret internal detail")

    response = client.post(
        "/admin/uploads/code-data",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        headers=auth_headers,
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Azure Blob Storage" in detail
    assert "boom" not in detail


def test_admin_upload_rag_data_happy_path(client, auth_headers, admin_upload_dirs, monkeypatch):
    """
    ingest_docs_in_vectordb is mocked out — same rule already applied to the
    LLM/E2B calls elsewhere in this suite: never run the real heavy pipeline
    (docling + live embeddings) in tests.
    """
    monkeypatch.setattr(
        "app.api.routers.admin.ingest_docs_in_vectordb",
        lambda pdf_path: {"skipped": False, "pages_ingested": 3},
    )

    response = client.post(
        "/admin/uploads/rag-data",
        files={"file": ("filing.pdf", b"%PDF-1.4 fake content", "application/pdf")},
        data={"company_name": "amazon", "doc_type": "10-k", "fiscal_year": "2024"},
        headers=auth_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "amazon 10-k 2024.pdf"
    assert body["pages_ingested"] == 3
    assert body["skipped"] is False
    assert (admin_upload_dirs.rag_dir / "amazon" / "amazon 10-k 2024.pdf").exists()