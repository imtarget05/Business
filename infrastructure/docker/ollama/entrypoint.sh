#!/bin/sh
# Ollama service entrypoint.
#
# Starts the ollama server, waits for it to be ready, pulls the model
# (if not already cached in the volume), then hands off to the server
# so the container stays alive.
set -e

MODEL="qwen2.5:3b"
OLLAMA_MODELS="/root/.ollama"

mkdir -p "$OLLAMA_MODELS/models"

# 1. Start the official ollama server in the background.
ollama serve &
SERVER_PID=$!

# 2. Wait until the server responds (up to ~30s).
echo "Waiting for ollama server to be ready..."
_wait_for_ready() {
  i=0
  until curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
      return 1
    fi
    sleep 1
  done
}
if _wait_for_ready; then
  echo "Ollama server ready (PID $SERVER_PID)."
else
  echo "ERROR: ollama server did not become ready in time"
  kill "$SERVER_PID" 2>/dev/null || true
  exit 1
fi

# 3. Pull the model only if it is not already cached.
if ! ollama list 2>/dev/null | grep -q "^${MODEL} "; then
  echo "Model ${MODEL} not found — pulling now (first start, ~5 min)..."
  ollama pull "${MODEL}"
  echo "Pull complete."
else
  echo "Model ${MODEL} already cached — skipping pull."
fi

# 4. Bring the server to the foreground so the container stays alive.
wait "$SERVER_PID"
