# Starts the FastAPI backend in dev mode. Run from anywhere:
#   .\scripts\run_backend_dev.ps1
# Assumes the backend venv is already activated and backend\.env is set up.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location (Join-Path $ProjectRoot "backend")
uvicorn app.main:app --reload --port 8000