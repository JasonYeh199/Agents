"use client";

import { FormEvent, useEffect, useState } from "react";
import ExecutionInspector from "../../components/ExecutionInspector";
import FiscalPeriodSelect from "../../components/FiscalPeriodSelect";
import TickerCombobox, { CompanyOption, NVIDIA_OPTION } from "../../components/TickerCombobox";

const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");
type Task = { id: string; capability: string; objective: string; status: string; checkpoint?: string };
type Project = { id: string; question: string; company: string; status: string; current_step?: string; progress: number; error?: string; plan: Task[]; budget: Record<string, number> };
type Report = { executive_summary: string; findings: { id: string; statement: string; interpretation: string; confidence: number; citations: { source_id: string; locator: string }[] }[]; bull_case: string; bear_case: string; disclaimer: string };

export default function Autonomous() {
  const [items, setItems] = useState<Project[]>([]);
  const [selected, setSelected] = useState("");
  const [company, setCompany] = useState<CompanyOption | null>(NVIDIA_OPTION);
  const [period, setPeriod] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const current = items.find(item => item.id === selected);

  async function load(preferred?: string) {
    const response = await fetch(`${API}/api/v1/autonomous-projects`);
    if (!response.ok) return;
    const data = await response.json(); setItems(data);
    if (preferred) setSelected(preferred); else if (!selected && data[0]) setSelected(data[0].id);
  }
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!current || !["queued", "running"].includes(current.status)) return;
    const timer = window.setTimeout(() => load(current.id), 700);
    return () => window.clearTimeout(timer);
  }, [current?.status, current?.id, current?.progress]);

  async function finish() {
    await load(selected);
    const response = await fetch(`${API}/api/v1/autonomous-projects/${selected}/report`);
    if (response.ok) setReport(await response.json());
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (!company || !period) { setError("請選擇支援公司與申報期間"); return; }
    setBusy(true); const form = new FormData(event.currentTarget);
    const response = await fetch(`${API}/api/v1/autonomous-projects`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question: form.get("question"), ticker: company.ticker, fiscal_period: period, language: form.get("language"), config: { max_tool_calls: Number(form.get("tools")), max_cost_usd: Number(form.get("cost")) } }) });
    const data = await response.json();
    if (response.ok) { setSelected(data.id); setReport(null); await load(data.id); } else setError(typeof data.detail === "string" ? data.detail : "建立專案失敗");
    setBusy(false);
  }
  async function action(name: string) { if (!current) return; await fetch(`${API}/api/v1/autonomous-projects/${current.id}/${name}`, { method: "POST" }); await load(current.id); }

  return <main><header><div className="brand">SIGNAL<span>FORGE</span></div><div className="badge">POC 05 · AUTONOMOUS ANALYST</div></header>
    <section className="auto-hero"><div><p className="eyebrow">LONG-HORIZON · CHECKPOINTED · BUDGETED</p><h1>讓 Agent 規劃，<em>也讓你看見每一步</em></h1><p className="lede">跨 Earnings、Thesis、Supply Chain 與 Debate 的自主研究，受工具、成本、重試及 checkpoint 預算約束。</p></div>
      <form className="auto-form" onSubmit={create}><textarea name="question" defaultValue="請評估 NVIDIA AI 基礎設施成長是否具備持續性，並提出可驗證的風險訊號。" required /><TickerCombobox value={company} onChange={setCompany} /><FiscalPeriodSelect ticker={company?.ticker} value={period} onChange={setPeriod} /><label>語言<select name="language"><option value="zh-TW">繁體中文</option><option value="en">English</option></select></label><label>Tool budget<input name="tools" type="number" defaultValue="40" /></label><label>Cost cap USD<input name="cost" type="number" defaultValue="5" /></label>{error && <p className="form-error">{error}</p>}<button disabled={busy || !company || !period}>{busy ? "建立中…" : "開始自主研究 →"}</button></form>
    </section>
    <section className="auto-bar"><b>PROJECT</b><select value={selected} onChange={event => { setSelected(event.target.value); setReport(null); }}><option value="">選擇專案</option>{items.map(item => <option key={item.id} value={item.id}>{item.company} · {item.question} · {item.status}</option>)}</select>{current && <><b>{current.status.toUpperCase()} · {current.progress}%</b><span>{current.current_step || "done"}</span><div className="auto-actions">{current.status === "running" && <button onClick={() => action("pause")}>PAUSE</button>}{["paused", "awaiting_retry", "failed"].includes(current.status) && <button onClick={() => action("resume")}>RESUME</button>}</div></>}</section>
    {current && <ExecutionInspector type="autonomous" id={current.id} onTerminal={status => status === "completed" && finish()} />}
    {current ? <section className="auto-work"><article className="auto-report"><p className="eyebrow">EXECUTION PLAN</p><div className="auto-plan">{current.plan.map(task => <div className={`auto-task ${task.status}`} key={task.id}><b>{task.capability.toUpperCase()}</b><p>{task.objective}</p><small>{task.status}{task.checkpoint ? ` · ${task.checkpoint}` : ""}</small></div>)}</div>{current.error && <p className="debate-error">{current.error}</p>}{report && <><p className="eyebrow">FINAL RESEARCH PROJECT</p><p className="summary">{report.executive_summary}</p>{report.findings.map(finding => <div className="finding" key={finding.id}><h3>{finding.statement} · {finding.confidence}%</h3><p>{finding.interpretation}</p><code>{finding.citations.map(citation => `${citation.source_id}:${citation.locator}`).join(" · ")}</code></div>)}<div className="twocol"><div><h3>Bull case</h3><p>{report.bull_case}</p></div><div><h3>Bear case</h3><p>{report.bear_case}</p></div></div><p className="disclaimer">{report.disclaimer}</p></>}</article><aside className="auto-side"><div className="panel"><p className="eyebrow">BUDGET</p><div className="stat"><b>{current.budget.used_tool_calls}/{current.budget.max_tool_calls}</b><span>TOOL CALLS</span></div><div className="stat"><b>${current.budget.estimated_cost_usd}/${current.budget.max_cost_usd}</b><span>COST</span></div></div></aside></section> : <section className="auto-empty"><h2>建立第一個自主研究專案</h2></section>}
  </main>;
}
