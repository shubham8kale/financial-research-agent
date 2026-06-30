export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col items-center justify-center gap-6 px-6 py-16 text-center">
      <span className="inline-flex items-center rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted">
        SEC 10-K Research
      </span>
      <h1 className="text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
        Financial Research Agent
      </h1>
      <p className="max-w-prose text-base text-muted">
        Streamed, source-grounded answers about SEC 10-K filings. The chat
        interface ships in the next phase.
      </p>
      <span className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground">
        Scaffold ready
      </span>
    </main>
  );
}
