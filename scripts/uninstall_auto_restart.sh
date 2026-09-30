#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Venice Key Manager // Auto-Restart Uninstaller (macOS & Linux)
# -----------------------------------------------------------------------------
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

$PYTHON_BIN "$PROJECT_ROOT/supervisor.py" --uninstall
echo "✅ Venice Key Manager auto-restart daemon uninstalled."
