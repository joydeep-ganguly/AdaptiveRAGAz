import io
from types import SimpleNamespace

import pytest
from azure.core.exceptions import AzureError, ResourceNotFoundError

from app.storage import code_data_store as store

MAX_MB = 1
_REAL_GET_CLIENT = store._get_container_client  # keep a handle on the real lru_cache'd function


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    settings = SimpleNamespace(
        azure_storage_account_name="acct",
        azure_storage_container="code-execution",
        azure_storage_code_data_prefix="code_data/",
        code_data_max_upload_mb=MAX_MB,
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

    def upload_blob(self, name, data, overwrite=False, content_settings=None):
        if self.error:
            raise self.error
        self.blobs[name] = data if isinstance(data, bytes) else data.read()
        self.uploads.append(
            SimpleNamespace(name=name, overwrite=overwrite, content_type=content_settings.content_type)
        )


@pytest.fixture()
def container(monkeypatch):
    fake = _FakeContainer(
        {
            "code_data/titanic.csv": b"a,b\n1,2\n",          # 8 bytes
            "code_data/apple_2024.xlsx": b"xlsx-bytes",      # 10 bytes
            "code_data/notes.txt": b"ignored: wrong extension",
            "code_data/nested/inner.csv": b"ignored: nested",
            "code_data/": b"",                               # folder placeholder
            "other_prefix/outside.csv": b"ignored: outside prefix",
        }
    )
    monkeypatch.setattr(store, "_get_container_client", lambda: fake)
    return fake


# ----------------------------------------------------------------- validation
@pytest.mark.parametrize("name", ["titanic.csv", "apple_2024.xlsx", "DATA.CSV", "My File.xlsx"])
def test_validate_filename_accepts_csv_and_xlsx(name):
    assert store.validate_filename(name) == name


@pytest.mark.parametrize(
    "name", ["", "..", ".", "../x.csv", "a/b.csv", "a\\b.csv", "notes.txt", "noextension", "x.csv\x00"]
)
def test_validate_filename_rejects_bad_names(name):
    with pytest.raises(store.InvalidCodeDataFilenameError):
        store.validate_filename(name)


# ------------------------------------------------------------------- listing
def test_list_files_returns_bare_names_sorted_and_filtered(container):
    assert store.list_files() == [
        store.CodeDataFile("apple_2024.xlsx", 10),
        store.CodeDataFile("titanic.csv", 8),
    ]


def test_list_files_wraps_azure_errors(container):
    container.error = AzureError("boom")
    with pytest.raises(store.CodeDataStorageError):
        store.list_files()


# ------------------------------------------------------------------ download
def test_download_returns_bytes(container):
    assert store.download("titanic.csv") == b"a,b\n1,2\n"


def test_download_missing_file_raises_not_found(container):
    with pytest.raises(store.CodeDataFileNotFoundError):
        store.download("missing.csv")


def test_download_rejects_invalid_name_before_touching_storage(container):
    container.error = AzureError("must not be reached")
    with pytest.raises(store.InvalidCodeDataFilenameError):
        store.download("../titanic.csv")


def test_download_refuses_oversized_file(container):
    container.blobs["code_data/big.csv"] = b"x" * (MAX_MB * 1024 * 1024 + 1)
    with pytest.raises(store.CodeDataFileTooLargeError):
        store.download("big.csv")


def test_download_wraps_azure_errors(container):
    container.error = AzureError("boom")
    with pytest.raises(store.CodeDataStorageError):
        store.download("titanic.csv")


# -------------------------------------------------------------------- upload
def test_upload_bytes_stores_under_prefix_with_overwrite_and_content_type(container):
    store.upload("new.csv", b"x,y\n1,2\n")

    assert container.blobs["code_data/new.csv"] == b"x,y\n1,2\n"
    upload = container.uploads[0]
    assert upload.name == "code_data/new.csv"
    assert upload.overwrite is True
    assert upload.content_type == "text/csv"


def test_upload_stream_sends_the_whole_file(container):
    stream = io.BytesIO(b"abc,def\n1,2\n")
    store.upload("stream.csv", stream)
    assert container.blobs["code_data/stream.csv"] == b"abc,def\n1,2\n"


def test_upload_xlsx_content_type(container):
    store.upload("sheet.xlsx", b"PK-fake")
    assert container.uploads[0].content_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_upload_silently_overwrites_existing_file(container):
    store.upload("titanic.csv", b"replaced")
    assert container.blobs["code_data/titanic.csv"] == b"replaced"


@pytest.mark.parametrize("make_data", [lambda n: b"x" * n, lambda n: io.BytesIO(b"x" * n)])
def test_upload_rejects_oversized_file_without_uploading(container, make_data):
    with pytest.raises(store.CodeDataFileTooLargeError):
        store.upload("big.csv", make_data(MAX_MB * 1024 * 1024 + 1))
    assert container.uploads == []


def test_upload_accepts_file_exactly_at_the_limit(container):
    store.upload("limit.csv", b"x" * (MAX_MB * 1024 * 1024))
    assert len(container.uploads) == 1


def test_upload_rejects_invalid_name_without_uploading(container):
    with pytest.raises(store.InvalidCodeDataFilenameError):
        store.upload("notes.txt", b"hello")
    assert container.uploads == []


def test_upload_wraps_azure_errors(container):
    container.error = AzureError("boom")
    with pytest.raises(store.CodeDataStorageError):
        store.upload("new.csv", b"x")


# -------------------------------------------------------------------- client
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
    assert first.container_name == "code-execution"


def test_missing_account_name_raises_storage_error(fake_settings):
    fake_settings.azure_storage_account_name = None
    with pytest.raises(store.CodeDataStorageError, match="AZURE_STORAGE_ACCOUNT_NAME"):
        store.list_files()