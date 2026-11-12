import io
from types import SimpleNamespace

import pytest
from azure.core.exceptions import AzureError, ResourceNotFoundError

from app.agent.schemas import DocType, FiscalQuarter
from app.storage import rag_data_store as store

MAX_MB = 1
MAX_BYTES = MAX_MB * 1024 * 1024
_REAL_GET_CLIENT = store._get_container_client  # keep a handle on the real lru_cache'd function

AMAZON_PDF = b"%PDF-amazon"      # 11 bytes
APPLE_PDF = b"%PDF-apple-8k"     # 13 bytes
GOOGLE_PDF = b"%PDF-google"      # 11 bytes


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    settings = SimpleNamespace(
        azure_storage_account_name="acct",
        azure_storage_rag_container="rag",
        azure_storage_rag_data_prefix="rag_data/",
        rag_data_max_upload_mb=MAX_MB,
    )
    monkeypatch.setattr(store, "get_settings", lambda: settings)
    _REAL_GET_CLIENT.cache_clear()
    yield settings
    _REAL_GET_CLIENT.cache_clear()


class _FakeDownloader:
    def __init__(self, data: bytes):
        self._data = data
        self.size = len(data)

    def readall(self) -> bytes:
        return self._data


class _FakeContainer:
    """Mirrors the few ContainerClient methods the store uses (same signatures)."""

    def __init__(self, blobs=None, error=None):
        self.blobs = dict(blobs or {})
        self.error = error
        self.uploads = []

    def list_blobs(self, name_starts_with=None):
        if self.error:
            raise self.error
        for name, data in list(self.blobs.items()):
            if name.startswith(name_starts_with or ""):
                yield SimpleNamespace(name=name, size=len(data))

    def download_blob(self, blob):
        if self.error:
            raise self.error
        if blob not in self.blobs:
            raise ResourceNotFoundError("blob not found")
        return _FakeDownloader(self.blobs[blob])

    def upload_blob(self, name, data, overwrite=False, content_settings=None, metadata=None):
        if self.error:
            raise self.error
        self.blobs[name] = data if isinstance(data, bytes) else data.read()
        self.uploads.append(
            SimpleNamespace(
                name=name,
                overwrite=overwrite,
                content_type=content_settings.content_type,
                metadata=metadata,
            )
        )


@pytest.fixture()
def container(monkeypatch):
    fake = _FakeContainer(
        {
            "rag_data/amazon/amazon 10-k 2023.pdf": AMAZON_PDF,
            "rag_data/apple/apple 8-k q4 2023.pdf": APPLE_PDF,
            "rag_data/google/google 10-k 2023.pdf": GOOGLE_PDF,
            "rag_data/amazon/": b"",                         # folder placeholder
            "rag_data/amazon/notes.txt": b"ignored: wrong extension",
            "rag_data/amazon/sub/deep.pdf": b"ignored: nested",
            "rag_data/stray.pdf": b"ignored: not inside a company folder",
            "rag_data/Bad Company/x.pdf": b"ignored: invalid company folder",
            "other_prefix/outside.pdf": b"ignored: outside prefix",
        }
    )
    monkeypatch.setattr(store, "_get_container_client", lambda: fake)
    return fake


def _pdf(size: int) -> bytes:
    return b"%PDF-" + b"x" * (size - len(b"%PDF-"))


# ------------------------------------------------------------------- file name
def test_build_filename_annual_report_has_no_quarter():
    assert store.build_filename("amazon", DocType.TEN_K, 2023) == "amazon 10-k 2023.pdf"


def test_build_filename_with_quarter():
    assert (
        store.build_filename("apple", DocType.EIGHT_K, 2023, FiscalQuarter.Q4)
        == "apple 8-k q4 2023.pdf"
    )


def test_build_filename_accepts_plain_strings_and_normalises_company():
    assert store.build_filename("  Google ", "10-q", 2024, "q1") == "google 10-q q1 2024.pdf"


@pytest.mark.parametrize("company", ["", "  ", "Amazon!", "two words", "../x", "a/b"])
def test_build_filename_rejects_bad_company(company):
    with pytest.raises(store.InvalidRagDataFieldsError, match="company_name"):
        store.build_filename(company, DocType.TEN_K, 2023)


def test_build_filename_rejects_unknown_doc_type_and_quarter():
    with pytest.raises(store.InvalidRagDataFieldsError, match="doc_type"):
        store.build_filename("amazon", "10-z", 2023)
    with pytest.raises(store.InvalidRagDataFieldsError, match="fiscal_quarter"):
        store.build_filename("amazon", "10-q", 2023, "q5")


@pytest.mark.parametrize("year", [1949, 2051, 0, -1, "2023", 2023.5, True])
def test_build_filename_rejects_bad_year(year):
    with pytest.raises(store.InvalidRagDataFieldsError, match="fiscal_year"):
        store.build_filename("amazon", DocType.TEN_K, year)


def test_parse_filename_annual_and_quarterly():
    assert store.parse_filename("amazon 10-k 2023.pdf") == store.RagDocumentFields(
        "amazon", "10-k", 2023, None
    )
    assert store.parse_filename("apple 8-k q4 2023.pdf") == store.RagDocumentFields(
        "apple", "8-k", 2023, "q4"
    )


@pytest.mark.parametrize(
    "name",
    [
        "amazon 10-k 2023.pdf",
        "amazon 10-q q1 2024.pdf",
        "google other 2020.pdf",
        "meta-platforms 10-k 2024.pdf",
    ],
)
def test_parse_filename_round_trips_with_build_filename(name):
    fields = store.parse_filename(name)
    assert (
        store.build_filename(fields.company_name, fields.doc_type, fields.fiscal_year, fields.fiscal_quarter)
        == name
    )


@pytest.mark.parametrize(
    "name",
    [
        "",
        "amazon 10-k.pdf",
        "Amazon 10-k 2023.pdf",
        "amazon 10-k  2023.pdf",
        "amazon 10-x 2023.pdf",
        "amazon 10-q q5 2024.pdf",
        "amazon 10-k 2023.txt",
        "amazon 10-k 2023.PDF",
        "amazon 10-k abcd.pdf",
        "amazon 10-k 02023.pdf",
        "amazon 10-k 1900.pdf",
        "a b c d e 2023.pdf",
    ],
)
def test_parse_filename_rejects_names_outside_the_convention(name):
    with pytest.raises(store.InvalidRagDataFieldsError):
        store.parse_filename(name)


# -------------------------------------------------------------------- listing
def test_list_files_returns_company_folder_pdfs_sorted_and_filtered(container):
    assert store.list_files() == [
        store.RagDataFile("amazon", "amazon 10-k 2023.pdf", len(AMAZON_PDF)),
        store.RagDataFile("apple", "apple 8-k q4 2023.pdf", len(APPLE_PDF)),
        store.RagDataFile("google", "google 10-k 2023.pdf", len(GOOGLE_PDF)),
    ]


def test_list_files_for_one_company(container):
    assert store.list_files("Apple") == [
        store.RagDataFile("apple", "apple 8-k q4 2023.pdf", len(APPLE_PDF)),
    ]


def test_list_files_unknown_company_is_empty(container):
    assert store.list_files("microsoft") == []


def test_list_files_rejects_invalid_company_before_touching_storage(container):
    container.error = AzureError("must not be reached")
    with pytest.raises(store.InvalidRagDataFieldsError):
        store.list_files("../amazon")


def test_list_files_wraps_azure_errors(container):
    container.error = AzureError("boom")
    with pytest.raises(store.RagDataStorageError):
        store.list_files()


# ------------------------------------------------------------------- download
def test_download_returns_bytes(container):
    assert store.download("amazon", "amazon 10-k 2023.pdf") == AMAZON_PDF


def test_download_missing_file_raises_not_found(container):
    with pytest.raises(store.RagDataFileNotFoundError):
        store.download("amazon", "amazon 10-k 1999.pdf")


@pytest.mark.parametrize("name", ["", "..", "../x.pdf", "a/b.pdf", "a\\b.pdf", "notes.txt", "x.pdf\x00"])
def test_download_rejects_invalid_name_before_touching_storage(container, name):
    container.error = AzureError("must not be reached")
    with pytest.raises(store.InvalidRagDataFieldsError):
        store.download("amazon", name)


def test_download_rejects_invalid_company_before_touching_storage(container):
    container.error = AzureError("must not be reached")
    with pytest.raises(store.InvalidRagDataFieldsError):
        store.download("../amazon", "amazon 10-k 2023.pdf")


def test_download_refuses_oversized_file(container):
    container.blobs["rag_data/amazon/amazon 10-k 2024.pdf"] = b"x" * (MAX_BYTES + 1)
    with pytest.raises(store.RagDataFileTooLargeError):
        store.download("amazon", "amazon 10-k 2024.pdf")


def test_download_wraps_azure_errors(container):
    container.error = AzureError("boom")
    with pytest.raises(store.RagDataStorageError):
        store.download("amazon", "amazon 10-k 2023.pdf")


# --------------------------------------------------------------------- upload
def test_upload_annual_report_blob_name_content_type_and_metadata(container):
    result = store.upload("filing.pdf", "amazon", DocType.TEN_K, 2024, None, _pdf(100))

    assert result == store.RagDataFile("amazon", "amazon 10-k 2024.pdf", 100)
    assert container.blobs["rag_data/amazon/amazon 10-k 2024.pdf"] == _pdf(100)

    upload = container.uploads[0]
    assert upload.name == "rag_data/amazon/amazon 10-k 2024.pdf"
    assert upload.overwrite is True
    assert upload.content_type == "application/pdf"
    assert upload.metadata == {"company_name": "amazon", "doc_type": "10-k", "fiscal_year": "2024"}


def test_upload_quarterly_report_adds_quarter_metadata(container):
    store.upload("x.pdf", "Apple", "10-q", 2024, FiscalQuarter.Q1, _pdf(100))

    upload = container.uploads[0]
    assert upload.name == "rag_data/apple/apple 10-q q1 2024.pdf"
    assert upload.metadata == {
        "company_name": "apple",
        "doc_type": "10-q",
        "fiscal_year": "2024",
        "fiscal_quarter": "q1",
    }


def test_upload_ignores_the_uploaded_file_name_apart_from_the_extension(container):
    store.upload("Whatever Name (3).PDF", "google", DocType.TEN_K, 2025, None, _pdf(50))
    assert "rag_data/google/google 10-k 2025.pdf" in container.blobs


def test_upload_stream_sends_the_whole_file(container):
    store.upload("x.pdf", "amazon", DocType.TEN_K, 2024, None, io.BytesIO(_pdf(200)))
    assert container.blobs["rag_data/amazon/amazon 10-k 2024.pdf"] == _pdf(200)


def test_upload_silently_overwrites_existing_file(container):
    store.upload("x.pdf", "amazon", DocType.TEN_K, 2023, None, _pdf(20))
    assert container.blobs["rag_data/amazon/amazon 10-k 2023.pdf"] == _pdf(20)


@pytest.mark.parametrize("make_data", [lambda n: _pdf(n), lambda n: io.BytesIO(_pdf(n))])
def test_upload_rejects_oversized_file_without_uploading(container, make_data):
    with pytest.raises(store.RagDataFileTooLargeError):
        store.upload("x.pdf", "amazon", DocType.TEN_K, 2024, None, make_data(MAX_BYTES + 1))
    assert container.uploads == []


def test_upload_accepts_file_exactly_at_the_limit(container):
    store.upload("x.pdf", "amazon", DocType.TEN_K, 2024, None, _pdf(MAX_BYTES))
    assert len(container.uploads) == 1


@pytest.mark.parametrize(
    "make_data",
    [
        lambda: b"",
        lambda: b"hello, not a pdf",
        lambda: b"PK\x03\x04 zip pretending to be a pdf",
        lambda: io.BytesIO(b"plain text"),
    ],
)
def test_upload_rejects_content_that_is_not_a_pdf(container, make_data):
    with pytest.raises(store.InvalidRagDataFieldsError, match="not a valid PDF"):
        store.upload("x.pdf", "amazon", DocType.TEN_K, 2024, None, make_data())
    assert container.uploads == []


@pytest.mark.parametrize("original", ["", "notes.txt", "filing", "filing.pdf.exe"])
def test_upload_rejects_non_pdf_extension(container, original):
    with pytest.raises(store.InvalidRagDataFieldsError, match="Only .pdf"):
        store.upload(original, "amazon", DocType.TEN_K, 2024, None, _pdf(20))
    assert container.uploads == []


def test_upload_rejects_bad_fields_without_uploading(container):
    with pytest.raises(store.InvalidRagDataFieldsError):
        store.upload("x.pdf", "Bad Company!", DocType.TEN_K, 2024, None, _pdf(20))
    with pytest.raises(store.InvalidRagDataFieldsError):
        store.upload("x.pdf", "amazon", DocType.TEN_K, 1800, None, _pdf(20))
    with pytest.raises(store.InvalidRagDataFieldsError):
        store.upload("x.pdf", "amazon", "not-a-type", 2024, None, _pdf(20))
    assert container.uploads == []


def test_upload_wraps_azure_errors(container):
    container.error = AzureError("boom")
    with pytest.raises(store.RagDataStorageError):
        store.upload("x.pdf", "amazon", DocType.TEN_K, 2024, None, _pdf(20))


# --------------------------------------------------------------------- client
def test_client_uses_account_url_default_credential_and_is_cached(monkeypatch):
    created = []

    class _FakeService:
        def __init__(self, account_url, credential=None):
            created.append((account_url, credential))

        def get_container_client(self, name):
            return SimpleNamespace(container_name=name)

    monkeypatch.setattr(store, "BlobServiceClient", _FakeService)
    monkeypatch.setattr(store, "DefaultAzureCredential", lambda: "fake-credential")

    first = _REAL_GET_CLIENT()
    second = _REAL_GET_CLIENT()

    assert created == [("https://acct.blob.core.windows.net", "fake-credential")]
    assert first is second
    assert first.container_name == "rag"


def test_missing_account_name_raises_storage_error(fake_settings):
    fake_settings.azure_storage_account_name = None
    with pytest.raises(store.RagDataStorageError, match="AZURE_STORAGE_ACCOUNT_NAME"):
        store.list_files()
