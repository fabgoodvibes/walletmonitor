#!/usr/bin/env bash
# run.sh — setup (first time) and launch the wallet monitor
# Usage: ./run.sh --wallet 0xYourAddress [--interval 15] [--basescan-key KEY]

set -e

VENV_DIR="$(dirname "$0")/.venv"
SCRIPT="$(dirname "$0")/wallet_monitor.py"

# ── First-time setup ──────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "🔧 First run — setting up virtual environment..."
    if ! python3 -m ensurepip --version &>/dev/null; then
        echo "📦 Installing python3-venv (requires sudo)..."
        sudo apt install -y python3-venv
    fi
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet rich requests
    echo "✅ Setup complete."
    echo ""
fi

# ── Launch ────────────────────────────────────────────────────────────────────
exec "$VENV_DIR/bin/python" "$SCRIPT" "$@"
