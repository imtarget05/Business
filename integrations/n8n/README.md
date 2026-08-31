# n8n Integration Layer

n8n is an **inbound integration layer only** — see ADR-003.

## Workflow: Inbound Task Relay (inbound-task-relay.json)

Phase 1 ships the first real workflow. Flow:

`	ext
External source (Slack / Gmail / Cron / custom webhook)
        ↓
  Webhook Trigger (POST /business-ops-relay)
        ↓
  HTTP Request → POST {BUSINESS_OPS_API_URL}/v1/tasks
        ↓
  IF response.status == "escalated"
   ├── YES → Slack Webhook alert with task_id + dashboard link
   └── NO  → No action (task completed normally)
`

### Environment variables (set in n8n)

| Variable | Purpose |
|---|---|
| BUSINESS_OPS_API_URL | Base URL of the API (default: http://host.docker.internal:8000) |
| BUSINESS_OPS_API_KEY | Value for X-API-Key header (must match .env API_KEY) |
| SLACK_WEBHOOK_URL | Slack incoming webhook for escalation alerts |
| DASHBOARD_URL | Dashboard base URL for deep links (default: http://localhost:3000) |

### Import

1. Open n8n UI → Workflows → Import from File
2. Select integrations/n8n/inbound-task-relay.json
3. Set environment variables in n8n Settings → Variables
4. Activate the workflow

### Contract

`	ext
Webhook / Slack / Gmail / Cron
        ↓  (n8n workflow)
POST {API_BASE_URL}/v1/tasks
Headers: X-API-Key, X-Request-ID
Body:    TaskRequest contract (packages/contracts/models.py)
`

### Rules for any n8n workflow

1. Nodes may only transform payloads into a valid TaskRequest and call the
   API. No reasoning, no prompts, no routing decisions.
2. Every execution must set X-Request-ID (or accept the response header) so
   runs are traceable in audit logs.
3. Workflow JSON exports belong in this directory for version control.

---

## Workflow: Gmail Inbound Auto-Reply (gmail-inbound-reply.json)

Phase 3 ships the Gmail auto-reply workflow. Flow:

`	ext
Gmail Trigger (poll unread emails in INBOX)
        ↓
Extract Email Data (Function node)
  → from_email, subject, body, conversation_payload
        ↓
POST /v1/conversations {channel: "email", subject}
  → returns {conversation_id, ...}
        ↓
POST /v1/conversations/{id}/messages {content: body}
  → returns {assistant_reply, actions[]}
        ↓
IF actions.some(a => a.mode === "SENT" || a.mode === "DRAFT")
  ├── YES → Gmail Send Reply (to from_email, threadId conserved)
  └── NO  → End (no auto-reply needed)
`

### Environment variables (set in n8n Settings → Variables)

| Variable | Purpose | Required |
|---|---|---|
| BASE_URL | Base URL of the Business Ops API | Yes |
| API_KEY | Value for X-API-Key header (must match .env API_KEY) | Yes |
| GMAIL_SENDER_EMAIL | (Optional) Email address to send replies from; defaults to OAuth account email | No |

### Gmail OAuth Credentials Setup

1. Go to **Google Cloud Console** → APIs & Services → Credentials
2. Create **OAuth 2.0 Client ID** (Application type: Web application)
3. Authorized redirect URIs: https://your-n8n-domain.com/rest/oauth2-credential/callback
4. Copy **Client ID** and **Client Secret**
5. In n8n: Settings → Credentials → New Credential → **Gmail OAuth2 API** → Paste Client ID/Secret
6. Save → **Copy Credential ID** (numeric, e.g., 123)
7. Open gmail-inbound-reply.json and replace YOUR_GMAIL_OAUTH_CREDENTIALS_ID in **both** places:
   - Gmail Trigger (Unread) node → credentials
   - Gmail Send Reply node → credentials

### Import & Activate

1. n8n UI → Workflows → Import from File → Select integrations/n8n/gmail-inbound-reply.json
2. Open workflow → Replace YOUR_GMAIL_OAUTH_CREDENTIALS_ID with actual Credential ID (2 nodes)
3. Set Environment Variables (see table above)
4. Click **Activate** (top right)

### Testing Checklist

- [ ] Send a test email to the connected Gmail inbox
- [ ] Check n8n Executions: workflow runs every minute, detects new email
- [ ] Check API logs: POST /v1/conversations → 201, POST /v1/conversations/{id}/messages → 200
- [ ] Verify response contains ctions array with at least one action where mode: "SENT" or "DRAFT"
- [ ] If action mode is SENT/DRAFT → Gmail Send Reply node executes, original sender receives reply
- [ ] Verify Gmail thread: reply appears in same thread (threadId preserved)
- [ ] Check audit logs: X-Request-ID traces to gmail_message_id for full traceability

### API Contract Reference (apps/api/routes/conversations.py)

**POST /v1/conversations**
- Request: ConversationCreateRequest — {channel: "email", subject?: string}
- Response: ConversationCreateResponse — {conversation_id: UUID, organization_id: UUID, channel: string, status: string, subject?: string}

**POST /v1/conversations/{id}/messages**
- Request: MessageCreateRequest — {content: string}
- Response: MessageCreateResponse — {conversation_id, user_message_id, assistant_message_id, assistant_reply: string, actions: ActionMetadata[]}
- ActionMetadata — {tool: string, arguments: object, result: string, mode?: string} — check mode === "SENT" || "DRAFT"

### Important Notes

- Workflow only processes **unread** emails in INBOX (poll interval: every minute)
- Each email creates a new conversation with channel: "email"
- Agent reply is sent automatically **only when** the send_email_reply tool returns mode: "SENT" or "DRAFT"
- X-Request-ID uses gmail_message_id for end-to-end audit traceability
- If API returns error → execution fails, n8n retries per configured retry policy
- Sticky notes in workflow contain Vietnamese setup guide (📋 Setup Guide) and flow diagram (🔧 Flow Logic)

---

## Workflow: AI Document Processor (i-document-processor.json)

AI-powered document processing workflow using local Ollama LLM for classification and intelligent routing.

### Overview
This workflow uses local Ollama LLM to automatically classify and route incoming documents. It demonstrates AI automation by combining n8n's orchestration with local LLM inference.

### Architecture
`	ext
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
`

### Setup
1. Install Ollama locally: https://ollama.com
2. Pull required models: ollama pull qwen2.5:3b
3. Import the workflow into n8n: Workflows → Import from File → Select i-document-processor.json
4. Configure environment variables (see table below)
5. Test with sample payload

### Environment variables (set in n8n Settings → Variables)

| Variable | Purpose | Required | Default |
|---|---|---|---|
| OLLAMA_URL | URL of Ollama API | Yes | http://host.docker.internal:11434 |
| OLLAMA_MODEL | Model for inference | Yes | qwen2.5:3b |
| API_BASE_URL | Base URL of Business Ops API | Yes | http://host.docker.internal:8000 |
| API_KEY | Value for X-API-Key header | Yes | — |
| SLACK_WEBHOOK_URL | Slack webhook for urgent notifications | No | — |
| DASHBOARD_URL | Dashboard base URL for deep links | No | http://localhost:3000 |

### Sample Payload
`json
{
  "text": "Customer complaint about delayed delivery...",
  "source": "email",
  "timestamp": "2024-01-15T10:30:00Z"
}
`

### AI Prompts Used

**Classification prompt:**
`
Classify the following document into exactly one category: support, sales, urgent, or general. Respond with only the category name, nothing else.

Document: {text}

Category:
`

**Summarization prompt:**
`
Summarize the following document in 2-3 sentences. Focus on key actions needed.

Document: {text}

Summary:
`

### Testing

`ash
# Test webhook endpoint
curl -X POST http://localhost:5678/webhook/ai-document-processor \
  -H 'Content-Type: application/json' \
  -d '{"text":"Customer complaint about delayed delivery","source":"email"}'

# Verify Ollama is accessible
curl http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5:3b","prompt":"hello","stream":false}'
`

### Error Handling

- **Valid Classification?** IF node checks if Ollama returned a response
- HTTP error codes: n8n auto-retries on 429/5xx (configure in node options)
- Timeout: 30s timeout for Ollama calls (local inference < 2s typical)
- Fallback: If classification is unclear, defaults to 'general' route

### Vietnamese Setup Guide (sticky notes in workflow)

The workflow includes sticky notes with Vietnamese instructions covering:
1. Ollama installation and model pulling
2. Environment variable configuration
3. Import and activation steps
4. Test payload examples
