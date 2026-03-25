$ErrorActionPreference = "Stop"

$rootPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$apiPath = Join-Path $rootPath "api"
$frontendPath = Join-Path $rootPath "frontend"
$apiPython = Join-Path $apiPath "env\Scripts\python.exe"

if (-not (Test-Path $apiPath)) {
    throw "Backend folder not found: $apiPath"
}

if (-not (Test-Path $frontendPath)) {
    throw "Frontend folder not found: $frontendPath"
}

if (-not (Test-Path $apiPython)) {
    throw "Backend Python not found at $apiPython. Create the venv first: python -m venv api\\env"
}

$backendCommand = "Set-Location '$rootPath'; & '$apiPython' -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000"
$frontendCommand = "Set-Location '$frontendPath'; npm run dev"

Write-Host "Launching backend window on http://127.0.0.1:8000" -ForegroundColor Green
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-Command", $backendCommand

Write-Host "Launching frontend window (typically http://127.0.0.1:5173)" -ForegroundColor Green
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-Command", $frontendCommand

Write-Host "Both services are launching..." -ForegroundColor Yellow
Write-Host "Backend docs: http://127.0.0.1:8000/docs" -ForegroundColor Yellow