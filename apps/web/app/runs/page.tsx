"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState, Suspense } from "react";
import {
  apiGet,
  getTaskTimeline,
  StatusBadge,
  type TaskStepView,
  type TaskView,
  type TimelineEvent,
} from "@/lib/api";

function RunsContent() {
  const params = useSearchParams();
  const searchTaskId = params.get("taskId") || "";
  const [taskIdInput, setTaskIdInput] = useState(searchTaskId);
  const [task, setTask] = useState<TaskView | null>(null);
  const [steps, setSteps] = useState<TaskStepView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Timeline state
  const [expandedStepId, setExpandedStepId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);

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
      setExpandedStepId(null); // Close any open timeline when loading new task
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setTask(null);
      setSteps([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchTimeline = async (taskId: string, stepId: string) => {
    setTimelineLoading(true);
    setTimelineError(null);
    try {
      const data = await getTaskTimeline(taskId);
      setTimeline(data.timeline);
      setExpandedStepId(stepId);
    } catch (err) {
      setTimelineError(err instanceof Error ? err.message : String(err));
      setTimeline([]);
      setExpandedStepId(stepId);
    } finally {
      setTimelineLoading(false);
    }
  };

  const handleTimelineClick = (taskId: string, stepId: string) => {
    if (expandedStepId === stepId) {
      setExpandedStepId(null);
      setTimeline([]);
    } else {
      fetchTimeline(taskId, stepId);
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
      <h1 className="text-lg md:text-xl font-semibold">Agent Runs</h1>
      <p className="mt-1 max-w-2xl text-xs md:text-sm text-slate-600">
        Per-agent execution timeline: trace any task through its orchestrator steps.
      </p>

      <form onSubmit={handleSubmit} className="mt-4 md:mt-6 flex flex-col md:flex-row gap-2">
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
        <div className="mt-4 rounded-md bg-red-50 p-3 md:p-4 text-xs md:text-sm text-red-700">
          {error}
        </div>
      )}

      {task && (
        <div className="mt-4 md:mt-6 rounded-lg border border-slate-200 bg-white p-4 md:p-5">
          <div className="flex flex-col md:flex-row md:items-center gap-2 md:gap-4">
            <h2 className="text-sm md:text-base font-semibold">Task {task.domain}.{task.action}</h2>
            <StatusBadge status={task.status} />
          </div>
          <p className="mt-1 font-mono text-xs text-slate-500">{task.task_id}</p>
          {task.error_code && (
            <p className="mt-2 text-xs md:text-sm text-red-600">
              [{task.error_code}] {task.error_message}
            </p>
          )}
        </div>
      )}

      {task && steps.length === 0 && (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 md:p-8 text-center text-xs md:text-sm text-slate-500">
          No steps recorded for this run.
        </div>
      )}

      {steps.length > 0 && (
        <div className="mt-4 space-y-2 md:space-y-3">
          {steps.map((s) => (
            <div
              key={s.id}
              className={`rounded-lg border border-slate-200 bg-white p-3 md:p-4 transition-colors cursor-pointer ${
                expandedStepId === s.id ? "border-blue-300 bg-blue-50" : "hover:bg-slate-50"
              }`}
              onClick={() => handleTimelineClick(task!.task_id, s.id)}
            >
              <div className="flex flex-wrap items-center gap-2 md:gap-3">
                <span className="font-mono text-xs text-slate-400">#{s.sequence}</span>
                <span className="text-xs md:text-sm font-medium">{s.name}</span>
                <StatusBadge status={s.status} />
                <span className="ml-auto font-mono text-xs text-slate-400">
                  {s.correlation_id ?? "—"}
                </span>
                <span className="text-slate-400 text-xs">
                  {expandedStepId === s.id ? "?" : "?"} Timeline
                </span>
              </div>
              {s.output && (
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-slate-50 p-2 md:p-3 font-mono text-xs text-slate-600">
                  {JSON.stringify(s.output, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}

      {expandedStepId && (
        <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-3 md:p-4">
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-xs md:text-sm font-semibold text-blue-900">Full Task Timeline</h3>
            {timelineLoading && <span className="text-xs text-blue-700 animate-pulse">Loading…</span>}
            {timelineError && (
              <span className="text-xs text-red-600">Error: {timelineError}</span>
            )}
          </div>
          {timeline.length === 0 && !timelineLoading && (
            <p className="text-xs md:text-sm text-slate-500">No timeline events found.</p>
          )}
          {timeline.length > 0 && (
            <div className="space-y-2">
              {timeline.map((event, idx) => (
                <div
                  key={`${event.stage}-${event.time}-${idx}`}
                  className="flex items-start gap-2 md:gap-3 text-xs md:text-sm"
                >
                  <div className="flex flex-col items-center min-w-[100px] md:min-w-[120px]">
                    <span className="font-mono text-xs text-slate-400">
                      {new Date(event.time).toLocaleTimeString()}
                    </span>
                    <span className="font-mono text-[10px] text-slate-400">
                      {new Date(event.time).toLocaleDateString()}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-block rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                          event.stage === "task"
                            ? "bg-slate-100 text-slate-700"
                            : event.stage === "step"
                            ? "bg-blue-100 text-blue-800"
                            : event.stage === "agent_run"
                            ? "bg-emerald-100 text-emerald-800"
                            : "bg-amber-100 text-amber-800"
                        }`}
                      >
                        {event.stage}
                      </span>
                      <StatusBadge status={event.status} />
                    </div>
                    <p className="mt-0.5 text-slate-600">{event.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!task && !loading && !error && (
        <div className="mt-6 md:mt-8 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 md:p-10 text-center text-xs md:text-sm text-slate-500">
          Enter a task UUID above to trace its execution.
        </div>
      )}
    </section>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={<div className="text-xs md:text-sm text-slate-500">Loading…</div>}>
      <RunsContent />
    </Suspense>
  );
}