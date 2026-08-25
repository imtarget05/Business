/**
 * Typed API client for the Business Ops API.
 *
 * The dashboard NEVER decides agent routing (ADR-006) — it only reads from
 * the backend `/v1/*` endpoints. All views must render honest empty states
 * when the API has no data; no mock/fake data is allowed.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: API_KEY ? { "X-API-Key": API_KEY } : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(`API error ${res.status}: ${path}`, res.status);
  }
  return res.json() as Promise<T>;
}

export async function apiSend<T>(
  path: string,
  method: "POST" | "DELETE" | "PUT",
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const errBody = (await res.json()) as { detail?: string };
      detail = typeof errBody.detail === "string" ? `: ${errBody.detail}` : "";
    } catch {
      // ignore malformed error bodies
    }
    throw new ApiError(`API error ${res.status}: ${path}${detail}`, res.status);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Shared view models (mirrors of backend /v1 responses)
// ---------------------------------------------------------------------------

export type TaskView = {
  task_id: string;
  domain: string;
  action: string;
  status: string;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error_code?: string | null;
  error_message?: string | null;
  correlation_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type TaskStepView = {
  id: string;
  task_id: string;
  sequence: number;
  name: string;
  status: string;
  output?: Record<string, unknown> | null;
  correlation_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type AgentView = {
  id: string;
  name: string;
  domain: string;
  version: string;
  description?: string | null;
  capabilities: string[];
  status?: string;
  timeout_ms?: number;
  max_retries?: number;
};

// ---------------------------------------------------------------------------
// Conversations (Phase 3 Task 3.5)
// ---------------------------------------------------------------------------

export type ConversationListItem = {
  conversation_id: string;
  organization_id: string;
  channel: string;
  status: string;
  subject?: string | null;
  updated_at?: string | null;
};

export type ConversationListResponse = {
  conversations: ConversationListItem[];
};

export type ConversationThreadResponse = {
  conversation_id: string;
  organization_id: string;
  channel: string;
  status: string;
  subject?: string | null;
  messages: MessageResponse[];
};

export type MessageResponse = {
  message_id: string;
  sequence: number;
  role: string;
  content: string;
  tool_metadata?: {
    actions: Array<{
      tool: string;
      arguments: Record<string, unknown>;
      result: string;
      mode?: string | null;
    }>;
  } | null;
};

export type MessageCreateRequest = {
  content: string;
};

export type MessageCreateResponse = {
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string;
  assistant_reply: string;
  actions: Array<{
    tool: string;
    arguments: Record<string, unknown>;
    result: string;
    mode?: string | null;
  }>;
};

export type ConversationCreateRequest = {
  channel: string;
  subject?: string;
};

export type ConversationCreateResponse = {
  conversation_id: string;
  organization_id: string;
  channel: string;
  status: string;
  subject?: string | null;
};

export const TASK_STATUSES = [
  "pending",
  "classifying",
  "routing",
  "running",
  "validating",
  "completed",
  "failed",
  "escalated",
  "cancelled",
] as const;

// ---------------------------------------------------------------------------
// Status badge colours (single source for all live views)
// ---------------------------------------------------------------------------

const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-800",
  success: "bg-emerald-100 text-emerald-800",
  succeeded: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
  escalated: "bg-amber-100 text-amber-800",
  rejected: "bg-orange-100 text-orange-800",
  timeout: "bg-orange-100 text-orange-800",
  pending: "bg-slate-200 text-slate-700",
  running: "bg-blue-100 text-blue-800",
  classifying: "bg-blue-100 text-blue-800",
  routing: "bg-blue-100 text-blue-800",
  validating: "bg-blue-100 text-blue-800",
  cancelled: "bg-slate-200 text-slate-500",
};

export function StatusBadge({ status }: { status: string }) {
  const style =
    STATUS_STYLES[status.toLowerCase()] ?? "bg-slate-200 text-slate-700";
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${style}`}
    >
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Conversation API helpers
// ---------------------------------------------------------------------------

export async function getConversations(): Promise<ConversationListResponse> {
  return apiGet<ConversationListResponse>("/v1/conversations");
}

export async function getConversation(
  id: string,
): Promise<ConversationThreadResponse> {
  return apiGet<ConversationThreadResponse>(`/v1/conversations/${id}`);
}

export async function createConversation(
  body: ConversationCreateRequest,
): Promise<ConversationCreateResponse> {
  return apiSend<ConversationCreateResponse>("/v1/conversations", "POST", body);
}

export async function sendMessage(
  conversationId: string,
  body: MessageCreateRequest,
): Promise<MessageCreateResponse> {
  return apiSend<MessageCreateResponse>(
    `/v1/conversations/${conversationId}/messages`,
    "POST",
    body,
  );
}
