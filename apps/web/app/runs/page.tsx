"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState, Suspense } from "react";
import {
  apiGet,
  StatusBadge,
  type TaskStepView,
  type TaskView,
} from "@/lib/api";

function RunsContent() {
  const params = useSearchParams();
  const searchTaskId = params.get("taskId") || "";
  const [taskIdInput, setTaskIdInput] = useState(searchTaskId);
  const [task, setTask] = useState<TaskView | null>(null);
  const [steps, setSteps] = useState<TaskStepView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTask = async (id: string) => {
    if (!id.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<{ task: TaskView; steps: TaskStepView[] }>(
        `/v1/tasks/${id.trim()}`
      );
      setTask(data.task);
      setSteps(data.steps);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setTask(null);
      setSteps([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (searchTaskId) {
      setTaskIdInput(searchTaskId);
      fetchTask(searchTaskId);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }
  }, [searchTaskId]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchTask(taskIdInput);
  };

  return (
    <section>
      <h1 className="text-xl font-semibold">Agent Runs</h1>
      <p className="mt-1 max-w-2xl text-sm text-slate-600">
        Per-agent execution timeline: trace any task through its orchestrator steps.
      </p>

      <form onSubmit={handleSubmit} className="mt-6 flex gap-2">
        <input
          type="text"
          placeholder="Task UUID…"
          value={taskIdInput}
          onChange={(e) => setTaskIdInput(e.target.value)}
          className="w-full max-w-md rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:bg-slate-400"
        >
          {loading ? "Loading…" : "Trace"}
        </button>
      </form>

      {error && (
        <div className="mt-4 rounded-md bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {task && (
        <div className="mt-6 rounded-lg border border-slate-200 bg-white p-5">
          <div className="flex items-center gap-4">
            <h2 className="text-base font-semibold">Task {task.domain}.{task.action}</h2>
            <StatusBadge status={task.status} />
          </div>
          <p className="mt-1 font-mono text-xs text-slate-500">{task.task_id}</p>
          {task.error_code && (
            <p className="mt-2 text-sm text-red-600">
              [{task.error_code}] {task.error_message}
            </p>
          )}
        </div>
      )}

      {task && steps.length === 0 && (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500">
          No steps recorded for this run.
        </div>
      )}

      {steps.length > 0 && (
        <div className="mt-4 space-y-3">
          {steps.map((s) => (
            <div key={s.id} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs text-slate-400">#{s.sequence}</span>
                <span className="text-sm font-medium">{s.name}</span>
                <StatusBadge status={s.status} />
                <span className="ml-auto font-mono text-xs text-slate-400">
                  {s.correlation_id ?? "—"}
                </span>
              </div>
              {s.output && (
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-slate-50 p-3 font-mono text-xs text-slate-600">
                  {JSON.stringify(s.output, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}

      {!task && !loading && !error && (
        <div className="mt-8 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-10 text-center text-sm text-slate-500">
          Enter a task UUID above to trace its execution.
        </div>
      )}
    </section>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={<div className="text-sm text-slate-500">Loading…</div>}>
      <RunsContent />
    </Suspense>
  );
}
