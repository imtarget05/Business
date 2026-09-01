"use client";

import { useState } from "react";
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

export default function MobileMenu() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  return (
    <div className="md:hidden">
      <header className="sticky top-0 z-40 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <Link href="/" className="block">
          <span className="text-sm font-bold tracking-tight">Business Ops</span>
          <span className="block text-xs text-slate-500">Agent Swarm</span>
        </Link>
        <button
          onClick={() => setOpen(!open)}
          className="rounded-md p-2 text-slate-700 hover:bg-slate-100"
          aria-label="Toggle menu"
          aria-expanded={open}
        >
          {open ? (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          )}
        </button>
      </header>

      {open && (
        <div className="fixed inset-0 z-30 flex">
          <div className="w-64 border-r border-slate-200 bg-white p-4 shadow-lg">
            <Link href="/" className="mb-6 block" onClick={() => setOpen(false)}>
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
                        onClick={() => setOpen(false)}
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
          </div>
          <div
            className="flex-1 bg-black/20"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
        </div>
      )}
    </div>
  );
}