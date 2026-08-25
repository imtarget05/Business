"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "@/lib/api";

type DocumentView = {
  id: string;
  title: string;
  status: string;
  chunk_count: number;
  source_type: string;
  created_at?: string | null;
};

type CitationView = {
  source_id: string;
  title: string;
  uri?: string;
  snippet?: string;
};

type QueryResponse = {
  answer: string;
  confidence: number;
  citations: CitationView[];
  refused_to_answer: boolean;
};

export default function KnowledgePage() {
  const [docs, setDocs] = useState<DocumentView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ingest form
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [ingesting, setIngesting] = useState(false);

  // query form
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<QueryResponse | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);

  const loadDocs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<{ documents: DocumentView[] }>("/v1/knowledge/documents");
      setDocs(data.documents);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  const handleIngest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !content.trim()) return;
    setIngesting(true);
    setError(null);
    try {
      await apiSend("/v1/knowledge/ingest", "POST", { title, content });
      setTitle("");
      setContent("");
      await loadDocs();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIngesting(false);
    }
  };

  const handleAsk = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setQueryError(null);
    setAnswer(null);
    try {
      const data = await apiSend<QueryResponse>("/v1/knowledge/query", "POST", {
        question,
      });
      setAnswer(data);
    } catch (err) {
      setQueryError(err instanceof Error ? err.message : String(err));
    } finally {
      setAsking(false);
    }
  };

  const handleDelete = async (id: string) => {
    setError(null);
    try {
      await apiSend(`/v1/knowledge/documents/${id}`, "DELETE");
      await loadDocs();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section>
      <div className="mb-6">
        <h1 className="text-xl font-semibold">Knowledge</h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-600">
          Ingest documents into the knowledge base and ask questions. Answers are
          citation-backed; when retrieval confidence is below the similarity
          threshold the agent returns &quot;no relevant information found&quot;
          instead of guessing.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 p-4 text-sm text-red-700">
          API error: {error}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Ingest */}
        <form
          onSubmit={handleIngest}
          className="rounded-lg border border-slate-200 bg-white p-4"
        >
          <h2 className="text-sm font-semibold">Ingest document</h2>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Document title"
            className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Paste document content…"
            rows={5}
            className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={ingesting || !title.trim() || !content.trim()}
            className="mt-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {ingesting ? "Ingesting…" : "Ingest"}
          </button>
        </form>

        {/* Query */}
        <form
          onSubmit={handleAsk}
          className="rounded-lg border border-slate-200 bg-white p-4"
        >
          <h2 className="text-sm font-semibold">Ask the knowledge base</h2>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. What is our refund policy?"
            rows={3}
            className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={asking || !question.trim()}
            className="mt-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {asking ? "Thinking…" : "Ask"}
          </button>

          {queryError && (
            <div className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700">
              {queryError}
            </div>
          )}

          {answer && (
            <div className="mt-3 rounded-md bg-slate-50 p-3 text-sm">
              <p
                className={
                  answer.refused_to_answer ? "font-medium text-amber-700" : "font-medium"
                }
              >
                {answer.answer}
              </p>
              {answer.citations.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-slate-600">
                  {answer.citations.map((c) => (
                    <li key={c.source_id}>
                      <span className="font-medium">{c.title}</span>
                      {c.snippet ? ` — ${c.snippet}` : ""}
                    </li>
                  ))}
                </ul>
              )}
              {!answer.refused_to_answer && (
                <p className="mt-2 text-xs text-slate-400">
                  confidence: {(answer.confidence * 100).toFixed(0)}%
                </p>
              )}
            </div>
          )}
        </form>
      </div>

      {/* Documents table */}
      <h2 className="mb-2 mt-8 text-sm font-semibold">Documents</h2>
      {loading ? (
        <div className="text-sm text-slate-500">Loading documents...</div>
      ) : docs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-10 text-center text-sm text-slate-500">
          No documents ingested yet.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-4 py-3 font-medium">Title</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Chunks</th>
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {docs.map((d) => (
                <tr key={d.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3">{d.title}</td>
                  <td className="px-4 py-3">{d.status}</td>
                  <td className="px-4 py-3 text-slate-500">{d.chunk_count}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {d.created_at ? new Date(d.created_at).toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => handleDelete(d.id)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
