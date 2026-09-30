#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Venice Key Manager // Auto-Restart & Boot Daemon Installer (macOS & Linux)
# Supports:
#   - macOS (Apple Silicon mcmini) via native launchd (~/Library/LaunchAgents)
#   - Linux via native systemd user service (~/.config/systemd/user)
# -----------------------------------------------------------------------------
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=========================================================="
echo "⚡ VENICE KEY MANAGER // AUTO-RESTART INSTALLER (UNIX)"
echo "=========================================================="
echo "Project Root: $PROJECT_ROOT"

# Detect Python 3
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "❌ Error: Python 3 not found in PATH. Please install Python 3.10+."
    exit 1
fi
echo "Using Python: $($PYTHON_BIN --version 2>&1)"

# Run python supervisor.py --install
$PYTHON_BIN "$PROJECT_ROOT/supervisor.py" --install

echo ""
$PYTHON_BIN "$PROJECT_ROOT/supervisor.py" --status

echo "✅ Venice Key Manager auto-restart daemon installed and enabled!"
