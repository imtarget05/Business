# n8n Error Handling Guide (N8N-001)

This document describes the error handling patterns to apply to each n8n workflow in this directory.

---

## Table of Contents

1. [General Error Handling Pattern](#general-error-handling-pattern)
2. [Workflow 1: inbound-task-relay.json](#workflow-1-inbound-task-relayjson)
3. [Workflow 2: gmail-inbound-reply.json](#workflow-2-gmail-inbound-replyjson)
4. [Workflow 3: ai-document-processor.json](#workflow-3-ai-document-processorjson)
5. [Node Configuration Reference](#node-configuration-reference)

---

## General Error Handling Pattern

Every HTTP Request node in these workflows should have:

### 1. Retry Configuration

Add to each HTTP Request node's `options` object:

```json
"options": {
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000
}
```

| Property | Value | Description |
|---|---|---|
| `retryOnFail` | `true` | Enables automatic retry on failure |
| `maxTries` | `3` | Maximum number of attempts (initial + 2 retries) |
| `waitBetweenTries` | `5000` | Wait 5 seconds between retries (ms) |

### 2. onError Property

Add to each HTTP Request node:

```json
"onError": "continueRegularOutput"
```

This ensures the node outputs an error object to the regular output instead of stopping execution, allowing downstream error handling.

### 3. Error Branch Pattern

After each HTTP Request node, add an IF node that checks for errors:

```json
{
  "name": "Check Error",
  "type": "n8n-nodes-base.if",
  "parameters": {
    "conditions": {
      "boolean": [
        {
          "value1": "={{ $json.error }}",
          "operation": "isNotEmpty"
        }
      ]
    }
  }
}
```

- **True branch** ? Error notification node (Slack webhook or NoOp with logging)
- **False branch** ? Continue normal flow

### 4. Error Notification Node

On the error branch, add either:

**Option A: Slack Webhook (recommended for production)**

```json
{
  "name": "Notify Error to Slack",
  "type": "n8n-nodes-base.httpRequest",
  "parameters": {
    "method": "POST",
    "url": "={{ $env.SLACK_WEBHOOK_URL }}",
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ text: '?? Workflow Error in [workflow name] at node [node name]: ' + $json.error }) }}"
  }
}
```

**Option B: NoOp with error logging (for non-critical paths)**

```json
{
  "name": "Log Error",
  "type": "n8n-nodes-base.noOp",
  "parameters": {}
}
```

---

## Workflow 1: inbound-task-relay.json

**Flow:** Webhook ? POST /v1/tasks ? IF(escalated) ? Slack

### Nodes Requiring Error Handling

| Node ID | Node Name | Type | Risk Level |
|---|---|---|---|
| `call-api` | POST /v1/tasks | HTTP Request | **HIGH** — External API call |
| `slack-notify` | Slack Escalation Alert | HTTP Request | MEDIUM — Notification failure |

### Error Handling to Add

#### 1. Node: `call-api` (POST /v1/tasks)

**Add retry configuration:**
```json
"options": {
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000
}
```

**Add onError property:**
```json
"onError": "continueRegularOutput"
```

**Add error check node after `call-api`:**

```json
{
  "id": "check-api-error",
  "name": "Check API Error",
  "type": "n8n-nodes-base.if",
  "typeVersion": 1,
  "position": [625, 300],
  "parameters": {
    "conditions": {
      "boolean": [
        {
          "value1": "={{ $json.error }}",
          "operation": "isNotEmpty"
        }
      ]
    }
  }
}
```

**Add error notification node:**

```json
{
  "id": "notify-api-error",
  "name": "Notify API Error",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "position": [750, 450],
  "parameters": {
    "method": "POST",
    "url": "={{ $env.SLACK_WEBHOOK_URL }}",
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ text: '?? Task Relay API Error: ' + ($json.error || 'Unknown error') + '\\nRequest ID: ' + ($json.request_id || $executionId) }) }}",
    "options": {
      "retryOnFail": true,
      "maxTries": 2,
      "waitBetweenTries": 3000
    }
  }
}
```

**Updated connections:**
```json
"call-api": {
  "main": [
    [{ "node": "Check API Error", "type": "main", "index": 0 }]
  ]
},
"Check API Error": {
  "main": [
    [{ "node": "Is Escalated?", "type": "main", "index": 0 }],
    [{ "node": "Notify API Error", "type": "main", "index": 0 }]
  ]
}
```

#### 2. Node: `slack-notify` (Slack Escalation Alert)

**Add retry configuration:**
```json
"options": {
  "retryOnFail": true,
  "maxTries": 2,
  "waitBetweenTries": 3000
}
```

**Add onError property:**
```json
"onError": "continueRegularOutput"
```

---

## Workflow 2: gmail-inbound-reply.json

**Flow:** Gmail Trigger ? Function ? POST conversations ? POST messages ? IF(auto-reply) ? Gmail Reply

### Nodes Requiring Error Handling

| Node ID | Node Name | Type | Risk Level |
|---|---|---|---|
| `create-conversation` | POST /v1/conversations | HTTP Request | **HIGH** — External API call |
| `append-message` | POST /v1/conversations/{id}/messages | HTTP Request | **HIGH** — External API call |
| `send-reply` | Gmail Send Reply | Gmail API | MEDIUM — Email send failure |

### Error Handling to Add

#### 1. Node: `create-conversation` (POST /v1/conversations)

**Add retry configuration:**
```json
"options": {
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000
}
```

**Add onError property:**
```json
"onError": "continueRegularOutput"
```

**Add error check node after `create-conversation`:**

```json
{
  "id": "check-conversation-error",
  "name": "Check Conversation Error",
  "type": "n8n-nodes-base.if",
  "typeVersion": 1,
  "position": [875, 200],
  "parameters": {
    "conditions": {
      "boolean": [
        {
          "value1": "={{ $json.error }}",
          "operation": "isNotEmpty"
        }
      ]
    }
  }
}
```

**Add error notification node:**

```json
{
  "id": "notify-conversation-error",
  "name": "Notify Conversation Error",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "position": [1000, 50],
  "parameters": {
    "method": "POST",
    "url": "={{ $env.SLACK_WEBHOOK_URL }}",
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ text: '?? Gmail Auto-Reply: Failed to create conversation\\nFrom: ' + $json.from_email + '\\nError: ' + ($json.error || 'Unknown') }) }}",
    "options": {
      "retryOnFail": true,
      "maxTries": 2,
      "waitBetweenTries": 3000
    }
  }
}
```

**Updated connections:**
```json
"create-conversation": {
  "main": [
    [{ "node": "Check Conversation Error", "type": "main", "index": 0 }]
  ]
},
"Check Conversation Error": {
  "main": [
    [{ "node": "POST /v1/conversations/{id}/messages", "type": "main", "index": 0 }],
    [{ "node": "Notify Conversation Error", "type": "main", "index": 0 }]
  ]
}
```

#### 2. Node: `append-message` (POST /v1/conversations/{id}/messages)

**Add retry configuration:**
```json
"options": {
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000
}
```

**Add onError property:**
```json
"onError": "continueRegularOutput"
```

**Add error check node after `append-message`:**

```json
{
  "id": "check-message-error",
  "name": "Check Message Error",
  "type": "n8n-nodes-base.if",
  "typeVersion": 1,
  "position": [1125, 200],
  "parameters": {
    "conditions": {
      "boolean": [
        {
          "value1": "={{ $json.error }}",
          "operation": "isNotEmpty"
        }
      ]
    }
  }
}
```

**Updated connections:**
```json
"append-message": {
  "main": [
    [{ "node": "Check Message Error", "type": "main", "index": 0 }]
  ]
},
"Check Message Error": {
  "main": [
    [{ "node": "Has Auto-Reply Action?", "type": "main", "index": 0 }],
    [{ "node": "Notify Conversation Error", "type": "main", "index": 0 }]
  ]
}
```

#### 3. Node: `send-reply` (Gmail Send Reply)

This is a Gmail API node (not HTTP Request), so it uses different error handling:

- Wrap in a Try-Catch pattern using n8n's error workflow
- Or add an Error Trigger workflow that catches failures

---

## Workflow 3: ai-document-processor.json

**Flow:** Webhook ? Ollama classify ? Ollama summarize ? Switch ? Actions ? Log

### Nodes Requiring Error Handling

| Node ID | Node Name | Type | Risk Level |
|---|---|---|---|
| `ollama-classify` | Ollama AI Classification | HTTP Request | **HIGH** — LLM service dependency |
| `ollama-summarize` | Ollama AI Summarization | HTTP Request | **HIGH** — LLM service dependency |
| `create-support-ticket` | Create Support Ticket | HTTP Request | MEDIUM |
| `create-sales-task` | Create Sales Task | HTTP Request | MEDIUM |
| `send-urgent-notification` | Send Urgent Notification | HTTP Request | MEDIUM |
| `save-to-database` | Save to Database | HTTP Request | MEDIUM |
| `log-result` | Log AI Decision | HTTP Request | LOW — Audit log |

### Error Handling to Add

#### 1. Node: `ollama-classify` (Ollama AI Classification)

**Add retry configuration:**
```json
"options": {
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "timeout": 30000
}
```

**Add onError property:**
```json
"onError": "continueRegularOutput"
```

**Add error check node after `ollama-classify`:**

```json
{
  "id": "check-classify-error",
  "name": "Check Classify Error",
  "type": "n8n-nodes-base.if",
  "typeVersion": 1,
  "position": [625, 300],
  "parameters": {
    "conditions": {
      "boolean": [
        {
          "value1": "={{ $json.error }}",
          "operation": "isNotEmpty"
        }
      ]
    }
  }
}
```

**Add error notification node:**

```json
{
  "id": "notify-classify-error",
  "name": "Notify Classify Error",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "position": [750, 500],
  "parameters": {
    "method": "POST",
    "url": "={{ $env.SLACK_WEBHOOK_URL }}",
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ text: '?? AI Document Processor: Classification failed\\nError: ' + ($json.error || 'Unknown') + '\\nDoc preview: ' + ($json.text || '').substring(0, 100) }) }}",
    "options": {
      "retryOnFail": true,
      "maxTries": 2,
      "waitBetweenTries": 3000
    }
  }
}
```

**Updated connections:**
```json
"ollama-classify": {
  "main": [
    [{ "node": "Check Classify Error", "type": "main", "index": 0 }]
  ]
},
"Check Classify Error": {
  "main": [
    [{ "node": "Valid Classification?", "type": "main", "index": 0 }],
    [{ "node": "Notify Classify Error", "type": "main", "index": 0 }]
  ]
}
```

#### 2. Node: `ollama-summarize` (Ollama AI Summarization)

**Add retry configuration:**
```json
"options": {
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "timeout": 30000
}
```

**Add onError property:**
```json
"onError": "continueRegularOutput"
```

**Add error check node after `ollama-summarize`:**

```json
{
  "id": "check-summarize-error",
  "name": "Check Summarize Error",
  "type": "n8n-nodes-base.if",
  "typeVersion": 1,
  "position": [1125, 200],
  "parameters": {
    "conditions": {
      "boolean": [
        {
          "value1": "={{ $json.error }}",
          "operation": "isNotEmpty"
        }
      ]
    }
  }
}
```

**Updated connections:**
```json
"ollama-summarize": {
  "main": [
    [{ "node": "Check Summarize Error", "type": "main", "index": 0 }]
  ]
},
"Check Summarize Error": {
  "main": [
    [{ "node": "Route by AI Decision", "type": "main", "index": 0 }],
    [{ "node": "Notify Classify Error", "type": "main", "index": 0 }]
  ]
}
```

#### 3. Action Nodes (create-support-ticket, create-sales-task, send-urgent-notification, save-to-database)

For each of these HTTP Request nodes, add:

```json
"onError": "continueRegularOutput",
"options": {
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000
}
```

#### 4. Node: `log-result` (Log AI Decision)

Since this is an audit log (non-critical), use lighter error handling:

```json
"onError": "continueRegularOutput",
"options": {
  "retryOnFail": true,
  "maxTries": 2,
  "waitBetweenTries": 2000
}
```

---

## Node Configuration Reference

### HTTP Request Node — Full Error-Handled Template

```json
{
  "id": "node-id",
  "name": "Node Name",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "position": [x, y],
  "onError": "continueRegularOutput",
  "parameters": {
    "method": "POST",
    "url": "https://api.example.com/endpoint",
    "sendHeaders": true,
    "headerParameters": { "parameters": [] },
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={ JSON.stringify({}) }",
    "options": {
      "retryOnFail": true,
      "maxTries": 3,
      "waitBetweenTries": 5000
    }
  }
}
```

### Error Check IF Node Template

```json
{
  "id": "check-error-node-id",
  "name": "Check [Node] Error",
  "type": "n8n-nodes-base.if",
  "typeVersion": 1,
  "position": [x, y],
  "parameters": {
    "conditions": {
      "boolean": [
        {
          "value1": "={{ $json.error }}",
          "operation": "isNotEmpty"
        }
      ]
    }
  }
}
```

### Error Notification HTTP Request Template

```json
{
  "id": "notify-error-node-id",
  "name": "Notify [Node] Error",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "position": [x, y],
  "parameters": {
    "method": "POST",
    "url": "={{ $env.SLACK_WEBHOOK_URL }}",
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ text: '?? [Workflow/Node]: ' + ($json.error || 'Unknown error') }) }}",
    "options": {
      "retryOnFail": true,
      "maxTries": 2,
      "waitBetweenTries": 3000
    }
  }
}
```

---

## Implementation Steps

1. **Open each workflow in n8n UI** (Workflows ? Import from File)
2. **For each HTTP Request node:**
   - Click the node ? Expand "Options" section
   - Enable "Retry on Fail"
   - Set Max Retries: 3
   - Set Wait Time Between Retries: 5000ms
3. **Add error check IF nodes** after each HTTP Request node
4. **Add error notification nodes** on the error branch
5. **Connect error branches** to notification nodes
6. **Test error scenarios** by temporarily pointing to invalid URLs
7. **Activate workflow** after verification

---

## Error Scenarios Covered

| Scenario | Handling |
|---|---|
| API timeout (408) | Retry up to 3 times with 5s delay |
| Rate limiting (429) | Retry up to 3 times with 5s delay |
| Server error (5xx) | Retry up to 3 times with 5s delay |
| Network failure | Retry up to 3 times with 5s delay |
| Invalid response | Error branch fires Slack notification |
| Ollama unavailable | Retry + error notification |
| Slack webhook down | Retry 2 times, then silent fail |

---

## Environment Variables Required

Ensure these are set in n8n Settings ? Variables:

| Variable | Used By | Purpose |
|---|---|---|
| `SLACK_WEBHOOK_URL` | All error notifications | Error alert channel |
| `DASHBOARD_URL` | Error messages | Deep link to dashboard |
| `BUSINESS_OPS_API_URL` | inbound-task-relay | API endpoint |
| `BASE_URL` | gmail-inbound-reply | API endpoint |
| `API_BASE_URL` | ai-document-processor | API endpoint |
| `OLLAMA_URL` | ai-document-processor | LLM service endpoint |
