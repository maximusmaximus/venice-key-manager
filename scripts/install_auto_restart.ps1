<#
.SYNOPSIS
  Installs auto-restart and boot services for Venice Key Manager on Windows.
.DESCRIPTION
  Configures silent startup VBScript in the Windows Startup folder and checks
  the supervisor watchdog to guarantee 24/7 continuous operation across system reboots.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "⚡ VENICE KEY MANAGER // AUTO-RESTART INSTALLER (WINDOWS)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Project Root: $ProjectRoot"

# Locate Python
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    Write-Error "Python was not found in PATH. Please install Python 3.10+."
    exit 1
}
Write-Host "Using Python: $PythonExe" -ForegroundColor Green

# Run python supervisor.py --install
& $PythonExe "$ProjectRoot\supervisor.py" --install

# Query status
Write-Host ""
& $PythonExe "$ProjectRoot\supervisor.py" --status

Write-Host "✅ Venice Key Manager auto-restart is active and configured for boot!" -ForegroundColor Green
