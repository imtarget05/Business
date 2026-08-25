"use client";

import { useEffect, useState } from "react";
import {
  apiGet,
  apiSend,
  StatusBadge,
  getConversations,
  getConversation,
  sendMessage,
  type ConversationListItem,
  type ConversationThreadResponse,
  type MessageResponse,
  type MessageCreateResponse,
} from "@/lib/api";

export default function InboxPage() {
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [thread, setThread] = useState<ConversationThreadResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [threadLoading, setThreadLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [composerInput, setComposerInput] = useState("");

  const loadConversations = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getConversations();
      setConversations(data.conversations);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const loadThread = async (conversationId: string) => {
    setThreadLoading(true);
    setThreadError(null);
    try {
      const data = await getConversation(conversationId);
      setThread(data);
    } catch (err) {
      setThreadError(err instanceof Error ? err.message : String(err));
      setThread(null);
    } finally {
      setThreadLoading(false);
    }
  };

  const handleConversationSelect = (conversationId: string) => {
    setSelectedConversationId(conversationId);
    loadThread(conversationId);
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!composerInput.trim() || !selectedConversationId || sending) return;

    const content = composerInput.trim();
    setComposerInput("");
    setSending(true);

    try {
      const response = await sendMessage(selectedConversationId, { content });
      // Update thread with new messages
      setThread((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          messages: [
            ...prev.messages,
            {
              message_id: response.user_message_id,
              sequence: prev.messages.length + 1,
              role: "user",
              content,
              tool_metadata: null,
            },
            {
              message_id: response.assistant_message_id,
              sequence: prev.messages.length + 2,
              role: "assistant",
              content: response.assistant_reply,
              tool_metadata: {
                actions: response.actions,
              },
            },
          ],
        };
      });
    } catch (err) {
      setThreadError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };

  const handleRefresh = () => {
    loadConversations();
    if (selectedConversationId) {
      loadThread(selectedConversationId);
    }
  };

  useEffect(() => {
    loadConversations();
  }, []);

  const formatDate = (dateString?: string | null) => {
    if (!dateString) return "—";
    try {
      return new Date(dateString).toLocaleString();
    } catch {
      return "—";
    }
  };

  const renderActions = (actions: Array<{ tool: string; arguments: Record<string, unknown>; result: string; mode?: string | null }> | undefined) => {
    if (!actions || actions.length === 0) return null;
    return (
      <div className="mt-2 flex flex-wrap gap-1.5">
        {actions.map((action, idx) => (
          <span
            key={`${action.tool}-${idx}`}
            className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-xs font-mono text-slate-700"
            title={`args: ${JSON.stringify(action.arguments)}; result: ${action.result.slice(0, 100)}`}
          >
            <span className="font-medium">{action.tool}</span>
            {action.mode && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">
                {action.mode}
              </span>
            )}
          </span>
        ))}
      </div>
    );
  };

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Inbox</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Support conversation threads. Select a thread to view messages and reply.
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={loading}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 p-4 text-sm text-red-700">
          Failed to load conversations: {error}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        {/* Left: Conversation List */}
        <div className="rounded-lg border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3">
            <h2 className="text-sm font-semibold">Conversations</h2>
          </div>

          {loading ? (
            <div className="p-6 text-center text-sm text-slate-500">Loading conversations...</div>
          ) : conversations.length === 0 ? (
            <div className="p-6 text-center text-sm text-slate-500">
              No conversations yet. Create one via the API to get started.
            </div>
          ) : (
            <div className="divide-y divide-slate-200">
              {conversations.map((conv) => (
                <button
                  key={conv.conversation_id}
                  onClick={() => handleConversationSelect(conv.conversation_id)}
                  className={`w-full px-4 py-3 text-left transition-colors ${
                    selectedConversationId === conv.conversation_id
                      ? "bg-blue-50 border-l-4 border-blue-600"
                      : "hover:bg-slate-50"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">
                          {conv.channel}
                        </span>
                        <StatusBadge status={conv.status} />
                      </div>
                      {conv.subject && (
                        <p className="mt-1 text-sm font-medium truncate">{conv.subject}</p>
                      )}
                      <p className="mt-1 text-xs text-slate-500">
                        Updated: {formatDate(conv.updated_at)}
                      </p>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Right: Chat View */}
        <div className="rounded-lg border border-slate-200 bg-white flex flex-col h-[600px]">
          {threadError && (
            <div className="m-4 rounded-md bg-red-50 p-4 text-sm text-red-700">
              Failed to load thread: {threadError}
            </div>
          )}

          {selectedConversationId === null ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center text-sm text-slate-500">
                Select a conversation from the list to view messages.
              </div>
            </div>
          ) : threadLoading ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-500">
              Loading messages...
            </div>
          ) : thread ? (
            <>
              {/* Thread Header */}
              <div className="border-b border-slate-200 px-4 py-3 flex items-center justify-between">
                <div>
                  <h2 className="font-semibold">{thread.subject || "Untitled conversation"}</h2>
                  <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                    <span className="rounded bg-blue-100 px-2 py-0.5 font-medium text-blue-800">
                      {thread.channel}
                    </span>
                    <StatusBadge status={thread.status} />
                    <span className="font-mono">
                      {thread.conversation_id.slice(0, 8)}…
                    </span>
                  </div>
                </div>
              </div>

              {/* Messages */}
              <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {thread.messages.length === 0 ? (
                  <div className="text-center text-sm text-slate-500 py-8">
                    No messages in this conversation yet.
                  </div>
                ) : (
                  thread.messages.map((msg) => (
                    <div
                      key={msg.message_id}
                      className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`max-w-[70%] rounded-2xl px-4 py-2 ${
                          msg.role === "user"
                            ? "bg-blue-600 text-white rounded-br-none"
                            : "bg-slate-100 text-slate-900 rounded-bl-none"
                        }`}
                      >
                        <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                        {msg.role === "assistant" && msg.tool_metadata?.actions && (
                          <div className="mt-2">
                            {renderActions(msg.tool_metadata.actions)}
                          </div>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>

              {/* Composer */}
              <div className="border-t border-slate-200 p-4">
                <form onSubmit={handleSendMessage} className="flex gap-2">
                  <input
                    type="text"
                    value={composerInput}
                    onChange={(e) => setComposerInput(e.target.value)}
                    placeholder="Type a message…"
                    disabled={sending}
                    className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm disabled:opacity-50"
                  />
                  <button
                    type="submit"
                    disabled={sending || !composerInput.trim()}
                    className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {sending ? "Sending…" : "Send"}
                  </button>
                </form>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-500">
              Conversation not found.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}