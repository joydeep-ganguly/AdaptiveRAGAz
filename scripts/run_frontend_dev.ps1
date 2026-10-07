# Starts the Streamlit frontend in dev mode. Run from anywhere:
#   .\scripts\run_frontend_dev.ps1
# Assumes the frontend deps are installed and frontend\.env is set up.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location (Join-Path $ProjectRoot "frontend")
streamlit run streamlit_app.py