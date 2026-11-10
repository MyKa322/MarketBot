<#
.SYNOPSIS
    Runs MarketBot with the project's virtualenv, whatever shell you are in.

.DESCRIPTION
    Picking the wrong Python is the most common way to hit "ModuleNotFoundError:
    No module named 'aiogram'". This script always uses .venv, creating it and
    installing dependencies on first run, so activation is never required.

.EXAMPLE
    .\run.ps1              # start the bot
    .\run.ps1 --check      # pre-flight checks
#>
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "No virtualenv found. Creating .venv ..." -ForegroundColor Cyan
    $systemPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $systemPython) {
        Write-Error "Python is not on PATH. Install Python 3.12+ and try again."
    }
    & $systemPython -m venv .venv
    Write-Host "Installing dependencies ..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r requirements.txt
}

if (-not (Test-Path -LiteralPath ".env")) {
    Write-Host "No .env found. Copying .env.example ..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "Add your BOT_TOKEN to .env, then run this again." -ForegroundColor Yellow
    exit 1
}

& $venvPython -m marketbot @args
exit $LASTEXITCODE
