#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install from https://brew.sh" >&2
  exit 1
fi

brew install ffmpeg meilisearch ollama

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install from https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv venv .venv
source .venv/bin/activate

uv pip install -e ".[dev,api]"
uv pip install marker-pdf

echo "Bootstrap complete."
echo "1) source .venv/bin/activate"
echo "2) larrak-audio doctor"
echo "3) larrak-audio ingest --source <file.pdf> --type pdf"
