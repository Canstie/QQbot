$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python virtual environment not found: $Python"
}

Set-Location $ProjectRoot
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name QQBotLauncher tools\qqbot_launcher.py

Write-Host "Built: $(Join-Path $ProjectRoot 'dist\QQBotLauncher.exe')"
