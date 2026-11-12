#!/usr/bin/env bash
# setup.sh — PathWatch AI one-command bootstrap
# Usage: ./setup.sh
# Satisfies AGENTS.md guardrail 5: one-command reproducibility.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "======================================================"
echo "  PathWatch AI — Setup"
echo "======================================================"

# ----------------------------------------------------------------------
# 1. Create virtual environment if it does not already exist
# ----------------------------------------------------------------------
if [ ! -d ".venv" ]; then
    echo "[1/4] Creating Python virtual environment (.venv) …"
    python3 -m venv .venv
else
    echo "[1/4] Virtual environment .venv already exists — skipping."
fi

# Activate
# shellcheck source=/dev/null
source .venv/bin/activate

# ----------------------------------------------------------------------
# 2. Install / upgrade dependencies (including python-multipart for
#    FastAPI file uploads required by web/app.py)
# ----------------------------------------------------------------------
echo "[2/4] Installing requirements …"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet python-multipart

echo "      ✓ All packages installed."

# ----------------------------------------------------------------------
# 3. Seed pathwatch.db with ADA/PROWAG standards and jurisdiction contacts
# ----------------------------------------------------------------------
echo "[3/4] Seeding pathwatch.db …"
python mcp_server/ada_seed.py

# ----------------------------------------------------------------------
# 4. Check that Ollama is installed and the moondream model is available
# ----------------------------------------------------------------------
echo "[4/4] Checking Ollama …"

if ! command -v ollama &> /dev/null; then
    echo "      ⚠️  ollama not found on PATH."
    echo "      Install Ollama from https://ollama.com/download, then run:"
    echo "        ollama pull moondream"
    echo "      PathWatch AI will not be able to audit images until Ollama is running."
else
    echo "      ✓ ollama found: $(ollama --version 2>/dev/null || echo 'version unknown')"

    # Check whether moondream is already pulled
    PATHWATCH_MODEL="${PATHWATCH_MODEL:-moondream}"
    if ollama list 2>/dev/null | grep -q "^${PATHWATCH_MODEL}"; then
        echo "      ✓ Model '${PATHWATCH_MODEL}' is available."
    else
        echo "      ⚠️  Model '${PATHWATCH_MODEL}' not found locally."
        echo "      Pull it with: ollama pull ${PATHWATCH_MODEL}"
    fi
fi

# ----------------------------------------------------------------------
echo ""
echo "======================================================"
echo "  Setup complete."
echo "  Start the dashboard with:"
echo "    source .venv/bin/activate && uvicorn web.app:app --reload"
echo "======================================================"
