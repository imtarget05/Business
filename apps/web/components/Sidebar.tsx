"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/tasks", label: "Tasks" },
  { href: "/agents", label: "Agents" },
  { href: "/runs", label: "Runs" },
  { href: "/knowledge", label: "Knowledge" },
  { href: "/evaluation", label: "Evaluation" },
  { href: "/audit", label: "Audit" },
];

export default function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="hidden md:block w-56 shrink-0 border-r border-slate-200 bg-white p-4">
      <Link href="/" className="mb-6 block">
        <span className="text-lg font-bold tracking-tight">Business Ops</span>
        <span className="block text-xs text-slate-500">Agent Swarm</span>
      </Link>
      <nav>
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={`block rounded-md px-3 py-2 text-sm ${
                    active
                      ? "bg-slate-900 text-white"
                      : "text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}