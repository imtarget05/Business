"use client";

import { useEffect, useState } from "react";
import { apiGet, StatusBadge, type AgentView } from "@/lib/api";

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    apiGet<{ agents: AgentView[] }>("/v1/agents")
      .then((data) => {
        if (active) setAgents(data.agents);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section>
      <div className="mb-6">
        <h1 className="text-lg md:text-xl font-semibold">Agents</h1>
        <p className="mt-1 max-w-2xl text-xs md:text-sm text-slate-600">
          Registered agents, versions, capabilities and status from the Agent Registry.
        </p>
      </div>

      {error ? (
        <div className="rounded-md bg-red-50 p-3 md:p-4 text-xs md:text-sm text-red-700">
          Failed to load agents: {error}
        </div>
      ) : loading ? (
        <div className="text-xs md:text-sm text-slate-500">Loading agents...</div>
      ) : agents.length === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 md:p-10 text-center text-xs md:text-sm text-slate-500">
          No agents registered in the registry.
        </div>
      ) : (
        <div className="grid gap-3 md:gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
          {agents.map((a) => (
            <div key={a.id} className="rounded-lg border border-slate-200 bg-white p-4 md:p-5">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold text-sm md:text-base">{a.name}</h3>
                  <p className="font-mono text-xs text-slate-500">{a.id}</p>
                </div>
                <StatusBadge status={a.status ?? "active"} />
              </div>

              <div className="mt-3 md:mt-4 text-xs md:text-sm">
                <div className="grid grid-cols-[100px_1fr] gap-2">
                  <span className="text-slate-500">Domain:</span>
                  <span className="font-medium">{a.domain}</span>

                  <span className="text-slate-500">Version:</span>
                  <span>{a.version}</span>
                </div>
              </div>

              <div className="mt-3 md:mt-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Capabilities</span>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {a.capabilities.map((cap) => (
                    <span key={cap} className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-700">
                      {cap}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}