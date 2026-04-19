#!/usr/bin/env bash
# build.sh — build Notion Bridge using Homebrew Python (not Anaconda)
# Usage:
#   ./build.sh          → alias build (fast, for dev)
#   ./build.sh release  → full standalone .app bundle

set -euo pipefail

PYTHON=/opt/homebrew/bin/python3
MODE=${1:-alias}

echo "→ Using Python: $PYTHON ($($PYTHON --version))"

# 1. Frontend
echo "→ Building frontend…"
(cd frontend && npm run build)

# 2. Clean previous build artifacts
echo "→ Cleaning dist/…"
rm -rf build dist

# 3. Bundle
if [[ "$MODE" == "release" ]]; then
    echo "→ py2app full build (this takes a while)…"
    "$PYTHON" setup.py py2app
else
    echo "→ py2app alias build (dev mode)…"
    "$PYTHON" setup.py py2app --alias
fi

echo "✓ Done — dist/Notion Bridge.app is ready"
echo "  Run: open \"dist/Notion Bridge.app\""
