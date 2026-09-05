"use client";
import { FormEvent, useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
type Citation = { source_id:string; claim_id:string; locator:string; supporting_excerpt:string };
type Fact = { id:string; label:string; value:string; period:string; citations:Citation[] };
type Report = { company:string; fiscal_period:string; language:string; executive_summary:string; sections:{title:string;claims:Fact[]}[]; catalysts:string[];risks:string[];sources:{id:string;url:string;publisher:string;document_type:string;published_at:string}[];disclaimer:string;canonical_facts_hash:string };
type Run = {id:string;company:string;fiscal_period:string;output_language:string;status:string;current_step?:string;progress:number;error?:string};
type Trace = {events:{sequence:number;kind:string;step:string;message:string;timestamp:string}[];provider:string;model:string;tool_calls:number;input_tokens:number;output_tokens:number;duration_ms:number};
type Eval = {passed:boolean;metrics:{name:string;value:number;threshold:number;passed:boolean}[]};

export default function Home() {
  const [company,setCompany]=useState("nvidia"), [period,setPeriod]=useState("FY2025-Q4"), [language,setLanguage]=useState("zh-TW");
  const [run,setRun]=useState<Run|null>(null), [report,setReport]=useState<Report|null>(null), [trace,setTrace]=useState<Trace|null>(null), [evaluation,setEvaluation]=useState<Eval|null>(null), [citation,setCitation]=useState<Citation|null>(null), [busy,setBusy]=useState(false);
  const periods = useMemo(()=>company==="nvidia"?["FY2025-Q4","FY2025-Q3"]:["FY2024-Q4","FY2024-Q3"],[company]);
  useEffect(()=>setPeriod(periods[0]),[periods]);
  useEffect(()=>{ if(!run || ["completed","awaiting_retry","failed"].includes(run.status)) return; const timer=setInterval(async()=>{const r=await fetch(`${API}/api/v1/research-runs/${run.id}`).then(x=>x.json());setRun(r);if(r.status==="completed"){setReport(await fetch(`${API}/api/v1/research-runs/${r.id}/report`).then(x=>x.json()));setTrace(await fetch(`${API}/api/v1/research-runs/${r.id}/trace`).then(x=>x.json()));}},500);return()=>clearInterval(timer)},[run]);
  async function launch(e:FormEvent){e.preventDefault();setBusy(true);setReport(null);setTrace(null);setEvaluation(null);const r=await fetch(`${API}/api/v1/research-runs`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({company,fiscal_period:period,output_language:language})});setRun(await r.json());setBusy(false)}
  async function evaluate(){if(!run)return;setEvaluation(await fetch(`${API}/api/v1/research-runs/${run.id}/evaluate`,{method:"POST"}).then(x=>x.json()))}
  async function retry(){if(!run)return;setRun(await fetch(`${API}/api/v1/research-runs/${run.id}/retry`,{method:"POST"}).then(x=>x.json()))}
  return <main>
    <header><div className="brand">SIGNAL<span>FORGE</span></div><div className="badge">POC 01 · EARNINGS INTELLIGENCE</div></header>
    <section className="hero"><div><p className="eyebrow">EVIDENCE-FIRST RESEARCH HARNESS</p><h1>把法說資料，鍛造成<br/><em>可追溯的投資洞察。</em></h1><p className="lede">官方來源、結構化事實、逐項引用。每一次 agent 推論都有軌跡，每一個數字都有根據。</p></div>
      <form onSubmit={launch} className="launcher"><label>公司<select value={company} onChange={e=>setCompany(e.target.value)}><option value="nvidia">NVIDIA</option><option value="tsmc">台積電 TSMC</option></select></label><label>財務季度<select value={period} onChange={e=>setPeriod(e.target.value)}>{periods.map(p=><option key={p}>{p}</option>)}</select></label><label>報告語言<select value={language} onChange={e=>setLanguage(e.target.value)}><option value="zh-TW">繁體中文</option><option value="en">English</option></select></label><button disabled={busy||run?.status==="running"}>{busy?"建立中…":"開始研究 →"}</button></form>
    </section>
    {run&&<section className="status"><div><b>{run.status.toUpperCase()}</b><span>{run.current_step||"pipeline complete"}</span></div><div className="bar"><i style={{width:`${run.progress}%`}}/></div><strong>{run.progress}%</strong>{run.error&&<p>{run.error}</p>}{run.status==="awaiting_retry"&&<button className="retry" onClick={retry}>從 checkpoint 重試</button>}</section>}
    {report&&<section className="workspace"><article className="report"><div className="report-head"><div><p className="eyebrow">{report.company.toUpperCase()} · {report.fiscal_period}</p><h2>{report.language==="zh-TW"?"法說研究報告":"Earnings research report"}</h2></div><button className="secondary" onClick={evaluate}>Run evaluation</button></div><p className="summary">{report.executive_summary}</p>{report.sections.map(section=><div className="section" key={section.title}><h3>{section.title}</h3>{section.claims.map(f=><div className="fact" key={f.id}><div><small>{f.period}</small><b>{f.label}</b></div><strong>{f.value}</strong><div>{f.citations.map(c=><button className="cite" key={c.claim_id} onClick={()=>setCitation(c)}>↗ {c.source_id}</button>)}</div></div>)}</div>)}<div className="twocol"><div><h3>Catalysts</h3>{report.catalysts.map(x=><p key={x}>＋ {x}</p>)}</div><div><h3>Risks</h3>{report.risks.map(x=><p key={x}>− {x}</p>)}</div></div><p className="disclaimer">{report.disclaimer}</p></article>
      <aside><div className="panel"><p className="eyebrow">RUN TELEMETRY</p>{trace&&<><div className="stat"><b>{trace.tool_calls}</b><span>tool calls</span></div><div className="stat"><b>{trace.provider}</b><span>{trace.model}</span></div><div className="stat"><b>{trace.duration_ms} ms</b><span>end-to-end</span></div><h4>Trajectory</h4>{trace.events.map(e=><div className="event" key={e.sequence}><i/><div><b>{e.step}</b><span>{e.message}</span></div></div>)}</>}</div>{evaluation&&<div className="panel eval"><p className="eyebrow">ACCEPTANCE GATE</p><h3 className={evaluation.passed?"pass":"fail"}>{evaluation.passed?"PASSED":"FAILED"}</h3>{evaluation.metrics.map(m=><div className="metric" key={m.name}><span>{m.name.replaceAll("_"," ")}</span><b>{(m.value*100).toFixed(0)}%</b></div>)}</div>}</aside>
    </section>}
    {citation&&<div className="overlay" onClick={()=>setCitation(null)}><div className="drawer" onClick={e=>e.stopPropagation()}><button className="close" onClick={()=>setCitation(null)}>×</button><p className="eyebrow">SOURCE EVIDENCE</p><h2>{citation.source_id}</h2><code>{citation.locator}</code><blockquote>“{citation.supporting_excerpt}”</blockquote><p>Claim ID · {citation.claim_id}</p></div></div>}
    <footer>SignalForge Research Systems <span>Research aid only · Not investment advice</span></footer>
  </main>
}
