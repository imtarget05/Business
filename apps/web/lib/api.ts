/**
 * Thin API client placeholder. Phase 1 will replace this with typed
 * fetchers for the Business Ops API (see docs/architecture/dashboard.md —
 * the dashboard never decides agent routing; it only calls the backend).
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${path}`);
  }
  return res.json() as Promise<T>;
}
