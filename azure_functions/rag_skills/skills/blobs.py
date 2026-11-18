"""Reading a PDF from blob storage with the Function app's managed identity."""

from urllib.parse import urlparse

from skills.errors import SkillError


def check_blob_url(url: str, allowed_host: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != allowed_host.lower():
        raise SkillError(f"The blob URL must be an https URL on {allowed_host}")
    return url


def download_blob(url: str, allowed_host: str, credential=None) -> bytes:
    check_blob_url(url, allowed_host)

    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient

    client = BlobClient.from_blob_url(url, credential=credential or DefaultAzureCredential())
    try:
        return client.download_blob().readall()
    except AzureError as exc:
        raise SkillError(f"Could not read the blob: {exc}") from exc
