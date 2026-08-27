#!/bin/sh
set -e

MODEL="qwen2.5:3b"
OLLAMA_MODELS="/root/.ollama"

# Ensure the OLLaMA models directory exists (mounted volume or fresh).
mkdir -p "$OLLAMA_MODELS/models"

# Check if the model is already pulled (cached in the volume).
if ! ollama list 2>/dev/null | grep -q "^${MODEL} "; then
  echo "Model ${MODEL} not found — pulling now (first start, ~5 min)..."
  ollama pull "${MODEL}"
  echo "Pull complete."
else
  echo "Model ${MODEL} already cached — skipping pull."
fi

# Hand off to the official ollama serve process.
exec ollama serve
