"use client";

import { useEffect, useState } from "react";
import { apiGet, StatusBadge, TASK_STATUSES, type TaskView } from "@/lib/api";

export default function TasksPage() {
  const [tasks, setTasks] = useState<TaskView[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const fetchTasks = async () => {
      setLoading(true);
      setError(null);
      try {
        const query = statusFilter !== "all" ? `?status=${statusFilter}` : "";
        const data = await apiGet<{ tasks: TaskView[] }>(`/v1/tasks${query}`);
        if (active) setTasks(data.tasks);
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (active) setLoading(false);
      }
    };
    fetchTasks();
    return () => {
      active = false;
    };
  }, [statusFilter]);

  return (
    <section>
      <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-lg md:text-xl font-semibold">Tasks</h1>
          <p className="mt-1 max-w-2xl text-xs md:text-sm text-slate-600">
            Task lifecycle monitor: pending ? classifying ? routing ? running ? validating ? terminal states.
          </p>
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
        >
          <option value="all">All Statuses</option>
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {error ? (
        <div className="rounded-md bg-red-50 p-3 md:p-4 text-xs md:text-sm text-red-700">
          Failed to load tasks: {error}
        </div>
      ) : loading ? (
        <div className="text-xs md:text-sm text-slate-500">Loading tasks...</div>
      ) : tasks.length === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 md:p-10 text-center text-xs md:text-sm text-slate-500">
          No tasks found{statusFilter !== "all" && ` for status "${statusFilter}"`}.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full divide-y divide-slate-200 text-left text-xs md:text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Task ID</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Domain / Action</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Status</th>
                <th className="px-3 md:px-4 py-2 md:py-3 font-medium">Created At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {tasks.map((t) => (
                <tr key={t.task_id} className="hover:bg-slate-50">
                  <td className="px-3 md:px-4 py-2 md:py-3 font-mono text-xs text-slate-500">
                    <a href={`/runs?taskId=${t.task_id}`} className="text-blue-600 hover:underline">
                      {t.task_id}
                    </a>
                  </td>
                  <td className="px-3 md:px-4 py-2 md:py-3">
                    {t.domain}.{t.action}
                  </td>
                  <td className="px-3 md:px-4 py-2 md:py-3">
                    <StatusBadge status={t.status} />
                  </td>
                  <td className="px-3 md:px-4 py-2 md:py-3 text-slate-500">
                    {t.created_at ? new Date(t.created_at).toLocaleString() : "—"}
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