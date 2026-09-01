"use client";

import { useEffect, useState } from "react";
import { apiGet, StatusBadge, type TaskStepView } from "@/lib/api";

export default function AuditPage() {
  const [steps, setSteps] = useState<TaskStepView[]>([]);
  const [correlationFilter, setCorrelationFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const fetchSteps = async () => {
      setLoading(true);
      setError(null);
      try {
        const q = correlationFilter
          ? `?correlation_id=${encodeURIComponent(correlationFilter)}&limit=100`
          : "?limit=100";
        const data = await apiGet<{ steps: TaskStepView[] }>(`/v1/steps${q}`);
        if (active) setSteps(data.steps);
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (active) setLoading(false);
      }
    };
    fetchSteps();
    return () => {
      active = false;
    };
  }, [correlationFilter]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // trigger re-fetch via state change handled by useEffect dependency
  };

  return (
    <section>
      <div className="mb-4 md:mb-6 flex flex-col md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-lg md:text-xl font-semibold">Audit Logs</h1>
          <p className="mt-1 max-w-2xl text-xs md:text-sm text-slate-600">
            Append-only audit trail of every orchestrator step, filterable by correlation ID.
          </p>
        </div>
      </div>

      <form
        onSubmit={handleSubmit}
        className="mb-4 md:mb-6 flex gap-2"
      >
        <input
          type="text"
          placeholder="Filter by correlation_id…"
          value={correlationFilter}
          onChange={(e) => setCorrelationFilter(e.target.value)}
          className="w-full max-w-sm rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
        />
      </form>

      {error ? (
        <div className="rounded-md bg-red-50 p-3 md:p-4 text-xs md:text-sm text-red-700">{error}</div>
      ) : loading ? (
        <div className="text-xs md:text-sm text-slate-500">Loading audit trail…</div>
      ) : steps.length === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 md:p-10 text-center text-xs md:text-sm text-slate-500">
          {correlationFilter
            ? `No steps found for correlation_id "${correlationFilter}".`
            : "No audit records yet. Run a task to see its steps here."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full divide-y divide-slate-200 text-left text-xs md:text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Step</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Task ID</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Name</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Status</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Correlation ID</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {steps.map((s) => (
                <tr key={s.id} className="hover:bg-slate-50">
                  <td className="px-3 md:px-4 py-2 md:py-3 font-mono text-xs text-slate-500">
                    #{s.sequence}
                  </td>
                  <td className="px-3 md:px-4 py-2 md:py-3 font-mono text-xs text-slate-500">
                    <a
                      href={`/runs?taskId=${s.task_id}`}
                      className="text-blue-600 hover:underline"
                    >
                      {s.task_id.slice(0, 8)}…
                    </a>
                  </td>
                  <td className="px-3 md:px-4 py-2 md:py-3 text-sm font-medium">{s.name}</td>
                  <td className="px-3 md:px-4 py-2 md:py-3">
                    <StatusBadge status={s.status} />
                  </td>
                  <td className="px-3 md:px-4 py-2 md:py-3 font-mono text-xs text-slate-500">
                    {s.correlation_id ?? "—"}
                  </td>
                  <td className="px-3 md:px-4 py-2 md:py-3 text-slate-500">
                    {s.started_at ? new Date(s.started_at).toLocaleString() : "—"}
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