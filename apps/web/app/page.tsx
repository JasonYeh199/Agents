"use client";

import { FormEvent, useEffect, useState } from "react";
import ExecutionInspector from "../components/ExecutionInspector";
import FiscalPeriodSelect from "../components/FiscalPeriodSelect";
import TickerCombobox, { CompanyOption, NVIDIA_OPTION } from "../components/TickerCombobox";

const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");
type Citation = { source_id: string; claim_id: string; locator: string; supporting_excerpt: string };
type Fact = { id: string; label: string; value: string; period: string; citations: Citation[] };
type Report = { company: string; ticker?: string; company_name?: string; fiscal_period: string; language: string; executive_summary: string; sections: { title: string; claims: Fact[] }[]; catalysts: string[]; risks: string[]; unverified: string[]; sources: { id: string; url: string; publisher: string; document_type: string; published_at: string }[]; disclaimer: string };
type Run = { id: string; ticker?: string; company: string; company_name?: string; fiscal_period: string; output_language: string; status: string; current_step?: string; progress: number; error?: string };
type Trace = { provider: string; model: string; tool_calls: number; input_tokens: number; output_tokens: number; duration_ms: number; profile_version_id?: string };
type Eval = { passed: boolean; metrics: { name: string; value: number; threshold: number; passed: boolean }[] };

export default function Home() {
  const [company, setCompany] = useState<CompanyOption | null>(NVIDIA_OPTION);
  const [period, setPeriod] = useState("");
  const [language, setLanguage] = useState("zh-TW");
  const [run, setRun] = useState<Run | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [evaluation, setEvaluation] = useState<Eval | null>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!run || ["completed", "awaiting_retry", "failed"].includes(run.status)) return;
    const timer = window.setInterval(async () => {
      const response = await fetch(`${API}/api/v1/research-runs/${run.id}`);
      if (response.ok) setRun(await response.json());
    }, 700);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  async function loadArtifacts(id: string) {
    const [reportResponse, traceResponse, runResponse] = await Promise.all([
      fetch(`${API}/api/v1/research-runs/${id}/report`), fetch(`${API}/api/v1/research-runs/${id}/trace`), fetch(`${API}/api/v1/research-runs/${id}`),
    ]);
    if (runResponse.ok) setRun(await runResponse.json());
    if (reportResponse.ok) setReport(await reportResponse.json());
    if (traceResponse.ok) setTrace(await traceResponse.json());
  }

  async function launch(event: FormEvent) {
    event.preventDefault(); setError("");
    if (!company) { setError("請從候選清單選擇支援的公司"); return; }
    if (!period) { setError("此公司目前沒有可用的申報期間"); return; }
    setBusy(true); setReport(null); setTrace(null); setEvaluation(null);
    const response = await fetch(`${API}/api/v1/research-runs`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ticker: company.ticker, fiscal_period: period, output_language: language }) });
    const data = await response.json();
    if (response.ok) setRun(data); else setError(typeof data.detail === "string" ? data.detail : "無法建立研究 run");
    setBusy(false);
  }

  async function evaluate() { if (!run) return; const response = await fetch(`${API}/api/v1/research-runs/${run.id}/evaluate`, { method: "POST" }); if (response.ok) setEvaluation(await response.json()); }
  async function retry() { if (!run) return; const response = await fetch(`${API}/api/v1/research-runs/${run.id}/retry`, { method: "POST" }); if (response.ok) setRun(await response.json()); }

  return <main>
    <header><div className="brand">SIGNAL<span>FORGE</span></div><div className="badge">POC 01 · EARNINGS INTELLIGENCE</div></header>
    <section className="hero"><div><p className="eyebrow">EVIDENCE-FIRST RESEARCH HARNESS</p><h1>從官方申報，<br /><em>看見 Agent 如何研究</em></h1><p className="lede">支援 TWSE 市值前 100 大與 Nasdaq-100。每個結論保留資料來源、工具軌跡、設定版本與可重現的執行紀錄。</p></div>
      <form onSubmit={launch} className="launcher"><TickerCombobox value={company} onChange={setCompany} /><FiscalPeriodSelect ticker={company?.ticker} value={period} onChange={setPeriod} /><label>輸出語言<select value={language} onChange={event => setLanguage(event.target.value)}><option value="zh-TW">繁體中文</option><option value="en">English</option></select></label>{error && <p className="form-error">{error}</p>}<button disabled={busy || !company || !period}>{busy ? "建立中…" : "開始研究 →"}</button></form>
    </section>
    {run && <section className="status"><div><b>{run.status.toUpperCase()}</b><span>{run.current_step || "pipeline complete"}</span></div><div className="bar"><i style={{ width: `${run.progress}%` }} /></div><strong>{run.progress}%</strong>{run.error && <p>{run.error}</p>}{run.status === "awaiting_retry" && <button className="retry" onClick={retry}>從 checkpoint 重試</button>}</section>}
    {run && <ExecutionInspector type="earnings" id={run.id} onTerminal={status => status === "completed" && loadArtifacts(run.id)} />}
    {report && <section className="workspace"><article className="report"><div className="report-head"><div><p className="eyebrow">{report.ticker || report.company} · {report.fiscal_period}</p><h2>{report.language === "zh-TW" ? "財報研究報告" : "Earnings research report"}</h2></div><button className="secondary" onClick={evaluate}>Run evaluation</button></div><p className="summary">{report.executive_summary}</p>
      {report.sections.map(section => <div className="section" key={section.title}><h3>{section.title}</h3>{section.claims.map(fact => <div className="fact" key={fact.id}><div><small>{fact.period}</small><b>{fact.label}</b></div><strong>{fact.value}</strong><div>{fact.citations.map(item => <button className="cite" key={`${item.claim_id}-${item.source_id}`} onClick={() => setCitation(item)}>↗ {item.source_id}</button>)}</div></div>)}</div>)}
      {!!report.unverified?.length && <div className="section unavailable"><h3>Unavailable / unverified</h3>{report.unverified.map(item => <p key={item}>— {item}</p>)}</div>}
      <div className="twocol"><div><h3>Catalysts</h3>{report.catalysts.map(item => <p key={item}>+ {item}</p>)}</div><div><h3>Risks</h3>{report.risks.map(item => <p key={item}>! {item}</p>)}</div></div><p className="disclaimer">{report.disclaimer}</p></article>
      <aside><div className="panel"><p className="eyebrow">RUN TELEMETRY</p>{trace && <><div className="stat"><b>{trace.tool_calls}</b><span>tool calls</span></div><div className="stat"><b>{trace.provider}</b><span>{trace.model}</span></div><div className="stat"><b>{trace.input_tokens + trace.output_tokens}</b><span>tokens · {trace.duration_ms} ms</span></div><div className="stat"><b>{trace.profile_version_id?.slice(0, 8) || "default"}</b><span>profile snapshot</span></div></>}</div>{evaluation && <div className="panel eval"><p className="eyebrow">ACCEPTANCE GATE</p><h3 className={evaluation.passed ? "pass" : "fail"}>{evaluation.passed ? "PASSED" : "FAILED"}</h3>{evaluation.metrics.map(metric => <div className="metric" key={metric.name}><span>{metric.name.replaceAll("_", " ")}</span><b>{(metric.value * 100).toFixed(0)}%</b></div>)}</div>}</aside>
    </section>}
    {citation && <div className="overlay" onClick={() => setCitation(null)}><div className="drawer" onClick={event => event.stopPropagation()}><button className="close" onClick={() => setCitation(null)}>×</button><p className="eyebrow">SOURCE EVIDENCE</p><h2>{citation.source_id}</h2><code>{citation.locator}</code><blockquote>“{citation.supporting_excerpt}”</blockquote><p>Claim ID · {citation.claim_id}</p></div></div>}
    <footer>SignalForge Research Systems <span>Research aid only · Not investment advice</span></footer>
  </main>;
}
