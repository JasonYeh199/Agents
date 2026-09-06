"use client";

import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");

export default function FiscalPeriodSelect({ ticker, value, onChange }: { ticker?: string; value: string; onChange: (period: string) => void }) {
  const [periods, setPeriods] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    request.current?.abort();
    setPeriods([]); setError("");
    if (!ticker) return;
    const controller = new AbortController(); request.current = controller; setLoading(true);
    fetch(`${API}/api/v1/companies/${encodeURIComponent(ticker)}/periods`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error("無法載入官方申報期間"); return response.json(); })
      .then(data => { if (!controller.signal.aborted) { setPeriods(data.periods || []); onChange(data.default_period || ""); } })
      .catch(cause => { if ((cause as Error).name !== "AbortError") setError((cause as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [ticker]);
  return <label className="period-field"><span>申報期間</span><select value={value} disabled={!ticker || loading} required onChange={event => onChange(event.target.value)}>
    <option value="">{loading ? "載入中…" : periods.length ? "請選擇" : "尚無可用期間"}</option>
    {periods.map(period => <option key={period}>{period}</option>)}
  </select>{error && <small className="ticker-error">{error}</small>}</label>;
}
