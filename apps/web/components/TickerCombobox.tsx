"use client";

import { KeyboardEvent, useEffect, useId, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");

export type CompanyOption = {
  ticker: string;
  name: string;
  market: "US" | "TW";
  exchange: string;
  rank: number;
  universe: string;
  universe_as_of?: string;
  aliases?: string[];
};

export const NVIDIA_OPTION: CompanyOption = {
  ticker: "NVDA", name: "NVIDIA Corporation", market: "US", exchange: "Nasdaq", rank: 1, universe: "nasdaq100",
};

type Props = {
  value: CompanyOption | null;
  onChange: (company: CompanyOption | null) => void;
  label?: string;
  required?: boolean;
};

export default function TickerCombobox({ value, onChange, label = "公司 ticker", required = true }: Props) {
  const listId = useId();
  const [query, setQuery] = useState(value?.ticker || "");
  const [results, setResults] = useState<CompanyOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    request.current?.abort();
    if (value && query === value.ticker) return;
    if (!query.trim()) {
      setResults([]); setLoading(false); setError(""); setOpen(false);
      return;
    }
    const timer = window.setTimeout(async () => {
      const controller = new AbortController();
      request.current = controller;
      setLoading(true); setError("");
      try {
        const response = await fetch(`${API}/api/v1/companies/search?q=${encodeURIComponent(query)}&limit=12`, { signal: controller.signal });
        if (!response.ok) throw new Error("搜尋服務暫時無法使用");
        const data = await response.json();
        setResults(data); setActive(0); setOpen(true);
        if (!data.length) setError("此 ticker 不在 TWSE 前 100 大或 Nasdaq-100 支援範圍內");
      } catch (cause) {
        if ((cause as Error).name !== "AbortError") setError((cause as Error).message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => { window.clearTimeout(timer); request.current?.abort(); };
  }, [query, value]);

  function choose(company: CompanyOption) {
    setQuery(company.ticker); onChange(company); setOpen(false); setError("");
  }

  function keyboard(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && results.length) { event.preventDefault(); setOpen(true); setActive(i => Math.min(i + 1, results.length - 1)); }
    if (event.key === "ArrowUp" && results.length) { event.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
    if (event.key === "Enter" && open && results[active]) { event.preventDefault(); choose(results[active]); }
    if (event.key === "Escape") setOpen(false);
  }

  return <label className="ticker-field">
    <span>{label}</span>
    <div className="ticker-input-wrap">
      <input
        value={query}
        required={required}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-busy={loading}
        aria-controls={listId}
        aria-activedescendant={open && results[active] ? `${listId}-${active}` : undefined}
        placeholder="輸入 NVDA、AAPL、2330…"
        onFocus={() => results.length && setOpen(true)}
        onChange={event => { setQuery(event.target.value); onChange(null); setOpen(true); }}
        onKeyDown={keyboard}
      />
      {loading && <i className="ticker-spinner" aria-label="搜尋中" />}
    </div>
    {open && <div id={listId} className="ticker-results" role="listbox">
      {loading && <p>搜尋 universe…</p>}
      {!loading && results.map((company, index) => <button
        type="button" role="option" aria-selected={index === active} id={`${listId}-${index}`}
        className={index === active ? "active" : ""} key={`${company.universe}-${company.ticker}`}
        onMouseDown={event => event.preventDefault()} onClick={() => choose(company)}
      >
        <b>{company.ticker}</b>
        <span><strong>{company.name}</strong><small>{company.exchange} · {company.market} · Rank #{company.rank}</small></span>
        <em>{company.universe === "twse100" ? "TWSE 100" : "NDX 100"}</em>
      </button>)}
      {!loading && !results.length && <p>沒有符合的支援公司</p>}
    </div>}
    {error && <small className="ticker-error">{error}</small>}
  </label>;
}
