import Link from "next/link";

export default function Home() {
  return (
    <div>
      <h1 className="text-xl md:text-2xl font-bold">Business Ops Agent Swarm</h1>
      <p className="mt-2 max-w-2xl text-xs md:text-sm text-slate-600">
        Multi-agent platform for business operations. Phase 0 shell — the
        orchestrator, agent registry and contracts live in the backend API.
      </p>
      <Link
        href="/dashboard"
        className="mt-6 inline-block rounded-md bg-slate-900 px-4 py-2 text-xs md:text-sm font-medium text-white hover:bg-slate-700"
      >
        Go to Dashboard ?
      </Link>
    </div>
  );
}