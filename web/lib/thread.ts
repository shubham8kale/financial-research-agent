/**
 * A conversation thread id for the backend's in-process memory (agent/memory.py).
 *
 * The server accepts ^[A-Za-z0-9_-]{8,64}$ and treats the id as an opaque key.
 * It is minted once per page load and again by "New chat"; it lives in React
 * state only (no localStorage, no sessionStorage, no cookie), so a reload is a
 * new conversation, which matches what the server does with it: it forgets a
 * thread after a while and everything when it restarts.
 */

export const THREAD_ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

/** Make *raw* satisfy the server's pattern: other characters become "_", length is held to 8 to 64. */
export function sanitizeThreadId(raw: string): string {
  const cleaned = raw.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 64);
  return cleaned.length >= 8 ? cleaned : cleaned.padEnd(8, "x");
}

/**
 * A fresh thread id. crypto.randomUUID() needs a secure context (https or
 * localhost), so fall back to getRandomValues, which does not, and last of all
 * to Math.random: an id is a conversation key, not a secret.
 */
export function newThreadId(): string {
  const c: Crypto | undefined = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (c && typeof c.randomUUID === "function") {
    return sanitizeThreadId(c.randomUUID());
  }
  if (c && typeof c.getRandomValues === "function") {
    const bytes = c.getRandomValues(new Uint8Array(16));
    return sanitizeThreadId(
      Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""),
    );
  }
  return sanitizeThreadId(
    `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`,
  );
}
