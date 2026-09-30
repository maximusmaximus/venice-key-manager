<#
.SYNOPSIS
  Uninstalls Venice Key Manager auto-restart boot services on Windows.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    Write-Error "Python not found in PATH."
    exit 1
}

& $PythonExe "$ProjectRoot\supervisor.py" --uninstall
Write-Host "✅ Venice Key Manager auto-restart boot services removed." -ForegroundColor Green
