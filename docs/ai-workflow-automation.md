# AI Workflow Automation

## Overview
This document describes the AI-powered workflow automation system that combines n8n (workflow orchestration) with Ollama (local LLM inference) for intelligent document processing.

## Architecture

### Components
1. **n8n** — Workflow orchestration engine
2. **Ollama** — Local LLM inference (qwen2.5:3b)
3. **Business Ops API** — FastAPI backend for task/conversation management

### Workflow: AI Document Processor

```text
Webhook Trigger (POST /ai-document-processor)
        ↓
Ollama AI Classification
  → POST {OLLAMA_URL}/api/generate
  → Returns: category (support/sales/urgent/general)
        ↓
Valid Classification? (IF node — error handling)
  ├── NO → End (error)
  └── YES ↓
        ↓
Ollama AI Summarization
  → POST {OLLAMA_URL}/api/generate
  → Returns: summary text
        ↓
Route by AI Decision (Switch node)
  ├── support → POST /v1/conversations (create support ticket)
  ├── sales   → POST /v1/tasks (create sales task)
  ├── urgent  → POST Slack webhook (send notification)
  └── general → POST /v1/tasks (save to database)
        ↓
Log AI Decision → POST /v1/tasks (audit log)
```

### AI Capabilities
- **Classification**: Categorizes incoming documents into support/sales/urgent/general
- **Summarization**: Generates concise summaries for quick triage
- **Routing**: Automatically routes to appropriate agent based on AI decision

### Benefits
- **Privacy**: All AI processing happens locally via Ollama
- **Cost**: No cloud LLM API costs for classification tasks
- **Speed**: Local inference < 2s for short documents
- **Automation**: Zero manual triage for routine documents

## Setup Guide

### Prerequisites
- Docker and Docker Compose (for containerized deployment)
- Ollama installed locally (for development)

### Step 1: Install Ollama
```bash
# macOS/Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows: Download from https://ollama.com
```

### Step 2: Pull Required Models
```bash
ollama pull qwen2.5:3b
```

### Step 3: Verify Ollama
```bash
# Test Ollama API
curl http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5:3b","prompt":"hello","stream":false}'
```

### Step 4: Configure n8n Environment Variables
In n8n: Settings → Variables

| Variable | Value |
|---|---|
| `OLLAMA_URL` | `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | `qwen2.5:3b` |
| `API_BASE_URL` | `http://host.docker.internal:8000` |
| `API_KEY` | (your API key) |
| `SLACK_WEBHOOK_URL` | (optional) |

### Step 5: Import Workflow
1. n8n UI → Workflows → Import from File
2. Select `integrations/n8n/ai-document-processor.json`
3. Click **Activate**

### Step 6: Test
```bash
curl -X POST http://localhost:5678/webhook/ai-document-processor \
  -H 'Content-Type: application/json' \
  -d '{"text":"Customer complaint about delayed delivery","source":"email"}'
```

## Metrics
- Classification accuracy: [to be measured]
- Average processing time: [to be measured]
- Documents processed per day: [to be measured]

## File Structure
```
integrations/n8n/
├── ai-document-processor.json    # n8n workflow definition
├── inbound-task-relay.json       # Phase 1 task relay
├── gmail-inbound-reply.json      # Phase 3 Gmail auto-reply
└── README.md                     # Documentation

scripts/
└── demo_ai_workflow.py           # Standalone demo script

docs/
└── ai-workflow-automation.md     # This file
```

## Demo Script
The `scripts/demo_ai_workflow.py` script demonstrates the AI workflow without n8n:

```bash
# Run with sample documents
python scripts/demo_ai_workflow.py

# Process custom text
python scripts/demo_ai_workflow.py --text "Your document text here"

# Process a file
python scripts/demo_ai_workflow.py --file document.txt

# Use a different model
python scripts/demo_ai_workflow.py --model llama3.2:1b
```

## Ollama API Reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/generate` | POST | Text generation (classification, summarization) |
| `/api/embeddings` | POST | Generate embeddings |
| `/api/chat` | POST | Chat completion |

### Example: Classification Request
```json
POST /api/generate
{
  "model": "qwen2.5:3b",
  "prompt": "Classify into: support, sales, urgent, or general\n\nDocument: ...",
  "stream": false
}
```

### Example: Summarization Request
```json
POST /api/generate
{
  "model": "qwen2.5:3b",
  "prompt": "Summarize in 2-3 sentences...\n\nDocument: ...",
  "stream": false
}
```
