/**
 * Frontend runtime config.
 *
 * NEXT_PUBLIC_API_BASE_URL points at the FRA FastAPI backend. It is inlined at
 * build time (NEXT_PUBLIC_ prefix), so it must be set before `next build`.
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080";
