import os
import time
import base64
import threading

import pandas as pd
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from e2b_code_interpreter import Sandbox

from app.config import get_settings
from app.storage import code_data_store

SANDBOX_IDLE_TIMEOUT_SECONDS = 20 * 60  # evict sandboxes idle longer than this


########################
# Helper Tool
########################
def get_dataset_info(filepath):
    if filepath.endswith == 'csv':
        df = pd.read_csv(filepath, nrows=3)
    else:
        df = pd.read_excel(filepath, nrows=3)
    return f"Columns : {list(df.columns)}\n Sample data:\n{df.to_string()}"


########################
# Per-conversation sandbox registry
########################
class _SandboxEntry:
    __slots__ = ("sandbox", "last_used")

    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox
        self.last_used = time.monotonic()


_sandboxes: dict[str, _SandboxEntry] = {}
_sandboxes_lock = threading.Lock()


def _evict_idle_sandboxes_locked() -> None:
    """Must be called while holding _sandboxes_lock."""
    now = time.monotonic()
    stale_ids = [
        conv_id
        for conv_id, entry in _sandboxes.items()
        if now - entry.last_used > SANDBOX_IDLE_TIMEOUT_SECONDS
    ]
    for conv_id in stale_ids:
        entry = _sandboxes.pop(conv_id)
        try:
            entry.sandbox.kill()
        except Exception:
            pass  # best-effort cleanup


def get_sandbox(conversation_id: str) -> Sandbox:
    """Get (or create) the E2B sandbox scoped to this conversation."""
    if not conversation_id:
        raise ValueError("conversation_id is required to obtain a sandbox")

    settings = get_settings()

    with _sandboxes_lock:
        _evict_idle_sandboxes_locked()

        entry = _sandboxes.get(conversation_id)
        if entry is not None and entry.sandbox.is_running():
            entry.last_used = time.monotonic()
            return entry.sandbox

        sbx = Sandbox.create(api_key=settings.e2b_api_key, timeout=SANDBOX_IDLE_TIMEOUT_SECONDS)
        _sandboxes[conversation_id] = _SandboxEntry(sbx)
        return sbx


def _conversation_id_from_config(config: RunnableConfig) -> str:
    conversation_id = (config or {}).get("configurable", {}).get("thread_id")
    if not conversation_id:
        raise ValueError("Missing thread_id/conversation_id in RunnableConfig")
    return conversation_id


########################
# Tools (config is auto-injected by LangChain — the LLM never supplies it)
########################
@tool
def list_code_data_files(name_filter: str = ""):
    """List the data files (.csv, .xlsx) available for analysis, with their sizes.
    Optionally pass part of a file name (case-insensitive) to narrow the list."""
    try:
        files = code_data_store.list_files()
    except code_data_store.CodeDataError as exc:
        return f"Error : {exc}"

    needle = name_filter.strip().lower()
    if needle:
        files = [f for f in files if needle in f.name.lower()]

    if not files:
        return "No matching data files found."

    lines = [f"- {f.name} ({f.size:,} bytes)" for f in files]
    return "Available data files:\n" + "\n".join(lines)


@tool
def upload_file(file_name: str, config: RunnableConfig):
    """Copy a data file from the shared data storage into the E2B sandbox for analysis.
    Use the exact file name shown by list_code_data_files."""
    conversation_id = _conversation_id_from_config(config)

    # Same leniency as before for a leading slash; anything else that isn't a bare
    # .csv/.xlsx name is rejected by code_data_store.
    file_name = file_name.strip().lstrip('/').lstrip('\\')

    # Download first so an unknown or invalid name never starts an E2B sandbox.
    try:
        data = code_data_store.download(file_name)
    except code_data_store.CodeDataFileNotFoundError as exc:
        return f"Error : {exc}. Use list_code_data_files to see the available files."
    except code_data_store.CodeDataError as exc:
        return f"Error : {exc}"

    sbx = get_sandbox(conversation_id)
    sandbox_file = sbx.files.write(f"data/{file_name}", data)

    return f"File uploaded successfully!\nSandbox path: {sandbox_file.path}"


@tool
def run_python_code(code: str, config: RunnableConfig):
    """Execute Python code in E2B sandbox."""
    settings = get_settings()
    conversation_id = _conversation_id_from_config(config)
    sbx = get_sandbox(conversation_id)

    print(f'Running code in sandbox for conversation {conversation_id}....')
    execution = sbx.run_code(code)
    print('Code execution is done!')

    if execution.error:
        return f"Error : {execution.error.name}\nValue : {execution.error.value}"

    media_dir = str(settings.resolved_media_dir)
    os.makedirs(media_dir, exist_ok=True)

    output = []
    timestamp = int(time.time())
    output.append(str(execution))

    for idx, result in enumerate(execution.results):
        if result.png:
            filename = os.path.join(media_dir, f"{conversation_id}_{timestamp}_chart_{idx}.png")
            with open(filename, 'wb') as f:
                f.write(base64.b64decode(result.png))
            output.append(f"Chart saved to {filename}")

    return '\n'.join(output) if output else "Code executed but no output was returned"