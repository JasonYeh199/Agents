"use client";

import { FormEvent, useEffect, useState } from "react";
import ExecutionInspector from "../../components/ExecutionInspector";
import FiscalPeriodSelect from "../../components/FiscalPeriodSelect";
import TickerCombobox, { CompanyOption, NVIDIA_OPTION } from "../../components/TickerCombobox";

const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");
type Metric = { name: string; value: number; unit: string };
type Result = { variant: { id: string; label: string; model: string; prompt_version: string }; passed: boolean; quality_score: number; metrics: Metric[]; trajectory: string[]; failure_reasons: string[] };
type Arena = { id: string; name: string; dataset: string; status: string; progress: number; results: Result[]; winner?: { variant_id: string; rationale: string }; events: { sequence: number; kind: string; message: string }[] };
const variants = [
  { id: "strict", label: "Evidence-first", model: "deterministic", prompt_version: "v1", skills: ["citation-auditor"], max_tool_calls: 20, citation_audit: true, critic_enabled: true },
  { id: "fast", label: "Fast baseline", model: "deterministic", prompt_version: "v1-lite", skills: [], max_tool_calls: 10, citation_audit: false, critic_enabled: false },
];

export default function ArenaPage() {
  const [items, setItems] = useState<Arena[]>([]);
  const [selected, setSelected] = useState("");
  const [company, setCompany] = useState<CompanyOption | null>(NVIDIA_OPTION);
  const [period, setPeriod] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const current = items.find(item => item.id === selected);
  async function load(preferred?: string) { const response = await fetch(`${API}/api/v1/evaluation-arenas`); if (!response.ok) return; const data = await response.json(); setItems(data); if (preferred) setSelected(preferred); else if (!selected && data[0]) setSelected(data[0].id); }
  useEffect(() => { load(); }, []);
  useEffect(() => { if (!current || !["queued", "running"].includes(current.status)) return; const timer = window.setTimeout(() => load(current.id), 600); return () => window.clearTimeout(timer); }, [current?.status, current?.id, current?.progress]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (!company || !period) { setError("請選擇支援公司與申報期間"); return; }
    setBusy(true); const form = new FormData(event.currentTarget);
    const response = await fetch(`${API}/api/v1/evaluation-arenas`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: form.get("name"), ticker: company.ticker, fiscal_period: period, variants }) });
    const data = await response.json(); if (response.ok) await load(data.id); else setError(typeof data.detail === "string" ? data.detail : "建立 Arena 失敗"); setBusy(false);
  }
  return <main><header><div className="brand">SIGNAL<span>FORGE</span></div><div className="badge">POC 06 · EVALUATION ARENA</div></header>
    <section className="arena-hero"><div><p className="eyebrow">SAME DATA · DIFFERENT HARNESS</p><h1>同一份證據，<em>比較 Agent 設計</em></h1><p className="lede">固定公司、期間與官方資料，比較 prompt、skills、工具預算、citation audit 與 critic 對輸出品質的影響。</p></div><form className="arena-form" onSubmit={create}><label>比較名稱<input name="name" defaultValue="Evidence-first vs Fast baseline" /></label><TickerCombobox value={company} onChange={setCompany} /><FiscalPeriodSelect ticker={company?.ticker} value={period} onChange={setPeriod} />{error && <p className="form-error">{error}</p>}<button disabled={busy || !company || !period}>{busy ? "建立中…" : "開始 Harness 比較 →"}</button></form></section>
    <section className="arena-picker"><b>ARENA RUN</b><select value={selected} onChange={event => setSelected(event.target.value)}><option value="">選擇比較</option>{items.map(item => <option key={item.id} value={item.id}>{item.name} · {item.dataset} · {item.status}</option>)}</select>{current && <b>{current.status.toUpperCase()} · {current.progress}%</b>}</section>
    {current && <ExecutionInspector type="arena" id={current.id} onTerminal={() => load(current.id)} />}
    {current ? <section className="arena-content">{current.winner && <div className="winner"><div><p className="eyebrow">WINNING HARNESS</p><strong>{current.winner.variant_id.toUpperCase()}</strong></div><p>{current.winner.rationale}</p></div>}<div className="variant-grid">{current.results.map(result => <article className={`variant-card ${current.winner?.variant_id === result.variant.id ? "win" : ""}`} key={result.variant.id}><p className="eyebrow">{result.variant.model} · {result.variant.prompt_version}</p><h2>{result.variant.label}</h2><div className="score">{result.quality_score}</div><b className={result.passed ? "pass" : "fail"}>{result.passed ? "PASSED" : "FAILED"}</b>{result.metrics.map(metric => <div className="arena-metric" key={metric.name}><span>{metric.name}</span><b>{metric.value} {metric.unit}</b></div>)}<h3>TRAJECTORY</h3><div className="trajectory">{result.trajectory.join(" → ")}</div>{result.failure_reasons.map(reason => <p className="failure" key={reason}>! {reason}</p>)}</article>)}</div></section> : <section className="arena-empty"><h2>建立第一個 Harness 比較</h2></section>}
  </main>;
}
