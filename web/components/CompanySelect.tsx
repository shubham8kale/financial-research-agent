"use client";

export interface Company {
  ticker: string;
  name: string;
}

// The five companies whose 10-K filings are indexed in the backend corpus
// (agent/financial_agent.py: INDEXED_TICKERS). Hardcoded for now per the spec.
export const COMPANIES: Company[] = [
  { ticker: "AAPL", name: "Apple" },
  { ticker: "MSFT", name: "Microsoft" },
  { ticker: "GOOGL", name: "Alphabet" },
  { ticker: "AMZN", name: "Amazon" },
  { ticker: "META", name: "Meta" },
];

interface CompanySelectProps {
  value: string | null;
  onChange: (ticker: string | null) => void;
  disabled?: boolean;
}

export default function CompanySelect({
  value,
  onChange,
  disabled,
}: CompanySelectProps) {
  return (
    <label className="flex items-center gap-2 text-sm text-muted">
      <span className="hidden sm:inline">Company</span>
      <select
        aria-label="Filter by company"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        disabled={disabled}
        className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-foreground shadow-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
      >
        <option value="">All companies</option>
        {COMPANIES.map((c) => (
          <option key={c.ticker} value={c.ticker}>
            {c.name} ({c.ticker})
          </option>
        ))}
      </select>
    </label>
  );
}
