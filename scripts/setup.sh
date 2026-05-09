#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example - fill in your Langfuse keys"
fi

mkdir -p data browser_profiles

echo ""
echo "setup complete. next:"
echo "  1. edit .env with your Langfuse keys"
echo "  2. ollama pull qwen3:8b   (or:  make ollama)"
echo "  3. make migrate"
echo "  4. make run"
