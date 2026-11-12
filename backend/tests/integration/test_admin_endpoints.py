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


_PDF = b"%PDF-1.4 fake content"


def _rag_form(**overrides):
    form = {"company_name": "amazon", "doc_type": "10-k", "fiscal_year": "2024"}
    form.update(overrides)
    return form


def _post_rag(client, headers, *, filename="filing.pdf", content=_PDF, **form_overrides):
    return client.post(
        "/admin/uploads/rag-data",
        files={"file": (filename, content, "application/pdf")},
        data=_rag_form(**form_overrides),
        headers=headers,
    )


def test_admin_upload_rag_data_happy_path(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers)

    assert response.status_code == 201
    assert response.json() == {"filename": "amazon 10-k 2024.pdf", "message": "File uploaded successfully."}
    assert fake_rag_data_blob.blobs["rag_data/amazon/amazon 10-k 2024.pdf"] == _PDF

    upload = fake_rag_data_blob.uploads[0]
    assert upload.overwrite is True
    assert upload.content_type == "application/pdf"
    assert upload.metadata == {"company_name": "amazon", "doc_type": "10-k", "fiscal_year": "2024"}


def test_admin_upload_rag_data_quarterly_filing(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers, doc_type="10-q", fiscal_quarter="q1", company_name=" Apple ")

    assert response.status_code == 201
    assert response.json()["filename"] == "apple 10-q q1 2024.pdf"
    upload = fake_rag_data_blob.uploads[0]
    assert upload.name == "rag_data/apple/apple 10-q q1 2024.pdf"
    assert upload.metadata == {
        "company_name": "apple",
        "doc_type": "10-q",
        "fiscal_year": "2024",
        "fiscal_quarter": "q1",
    }


def test_admin_upload_rag_data_ignores_the_uploaded_file_name(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers, filename="Scan (3).PDF")

    assert response.status_code == 201
    assert response.json()["filename"] == "amazon 10-k 2024.pdf"


def test_admin_upload_rag_data_overwrites_existing_file(client, auth_headers, fake_rag_data_blob):
    for content in (b"%PDF-first", b"%PDF-second"):
        assert _post_rag(client, auth_headers, content=content).status_code == 201

    assert fake_rag_data_blob.blobs["rag_data/amazon/amazon 10-k 2024.pdf"] == b"%PDF-second"


def test_admin_upload_rag_data_rejects_non_pdf_extension(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers, filename="notes.txt")

    assert response.status_code == 400
    assert response.json()["detail"] == "Only .pdf files are allowed"
    assert fake_rag_data_blob.uploads == []


def test_admin_upload_rag_data_rejects_content_that_is_not_a_pdf(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers, content=b"just some text")

    assert response.status_code == 400
    assert response.json()["detail"] == "File is not a valid PDF"
    assert fake_rag_data_blob.uploads == []


def test_admin_upload_rag_data_rejects_bad_company_name(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers, company_name="Bad Company!")

    assert response.status_code == 400
    assert "company_name" in response.json()["detail"]
    assert fake_rag_data_blob.uploads == []


def test_admin_upload_rag_data_rejects_out_of_range_year(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers, fiscal_year="1800")

    assert response.status_code == 400
    assert "fiscal_year" in response.json()["detail"]
    assert fake_rag_data_blob.uploads == []


def test_admin_upload_rag_data_rejects_unknown_doc_type(client, auth_headers, fake_rag_data_blob):
    response = _post_rag(client, auth_headers, doc_type="10-z")

    assert response.status_code == 422  # rejected by the DocType enum before the route body runs
    assert fake_rag_data_blob.uploads == []


def test_admin_upload_rag_data_rejects_oversized_file(client, auth_headers, fake_rag_data_blob):
    too_big = b"%PDF-" + b"x" * (1024 * 1024)  # fixture limit is 1 MB
    response = _post_rag(client, auth_headers, content=too_big)

    assert response.status_code == 413
    assert fake_rag_data_blob.uploads == []


def test_admin_upload_rag_data_storage_failure_returns_502(client, auth_headers, fake_rag_data_blob):
    fake_rag_data_blob.error = AzureError("boom: secret internal detail")

    response = _post_rag(client, auth_headers)

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Azure Blob Storage" in detail
    assert "boom" not in detail


def test_admin_upload_rag_data_no_longer_ingests_locally():
    from app.api.routers import admin

    assert not hasattr(admin, "ingest_docs_in_vectordb")
