"use client";

import { useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");
type Profile = { id: string; poc_type: string; name: string; version: number; status: string; config: Record<string, unknown>; validation_errors: string[]; created_at: string };
type Execution = { type: string; id: string; status: string; ticker?: string; label: string; created_at: string };
type Universe = { id: string; universe: string; version: string; as_of_date: string; source_url: string; content_hash: string; member_count: number; issuer_count: number; source_status: "bootstrap" | "verified"; sync_error?: string; artifacts: { source_url: string; object_key: string; content_hash: string }[] };
type Audit = { id: string; action: string; actor: string; target_type: string; target_id?: string; detail: Record<string, unknown>; created_at: string };

async function api(path: string, init?: RequestInit) {
  const response = await fetch(`${API}${path}`, { credentials: "include", ...init, headers: { "content-type": "application/json", ...(init?.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || "Request failed"));
  return data;
}

function TraceDetail({ trace }: { trace: Record<string, unknown> }) {
  const [filter, setFilter] = useState("all");
  const events = (trace.events || []) as { sequence: number; kind: string; step?: string; message: string; payload?: Record<string, unknown> }[];
  const tools = (trace.tool_calls || []) as { sequence: number; kind: string; step?: string; message: string; payload?: Record<string, unknown> }[];
  const summaries = (trace.reasoning_summaries || []) as (string | { text?: string })[];
  const checkpoints = (trace.checkpoints || []) as string[];
  const sources = (trace.sources || []) as { id?: string; publisher?: string; document_type?: string; url?: string; sha256?: string; object_key?: string; parser_version?: string }[];
  const usage = (trace.usage || {}) as Record<string, number>;
  const config = (trace.config_snapshot || {}) as Record<string, unknown>;
  const pipeline = (config.pipeline || []) as { id: string; enabled: boolean; depends_on: string[] }[];
  const visible = filter === "all" ? events : events.filter(event => event.kind.startsWith(filter));
  return <div className="trace-detail">
    {Boolean(trace.error) && <div className="trace-error"><b>EXECUTION ERROR</b><span>{String(trace.error)}</span></div>}
    <div className="trace-stats">{[["STATUS", trace.status], ["PROVIDER", trace.provider || "n/a"], ["MODEL", trace.model || "n/a"], ["INPUT TOKENS", usage.input_tokens || 0], ["OUTPUT TOKENS", usage.output_tokens || 0], ["TOOL CALLS", usage.tool_calls || 0], ["COST USD", usage.estimated_cost_usd || 0], ["DURATION", `${usage.duration_ms || 0} ms`]].map(([label, value]) => <div key={String(label)}><small>{String(label)}</small><b>{String(value)}</b></div>)}</div>
    <section><p className="eyebrow">AGENT DAG</p><div className="console-dag">{pipeline.filter(node => node.enabled).map(node => <div key={node.id}><b>{node.id}</b><small>{node.depends_on.length ? `← ${node.depends_on.join(", ")}` : "root"}</small></div>)}</div></section>
    <section><p className="eyebrow">REASONING / DECISION SUMMARIES</p>{summaries.length ? summaries.map((summary, index) => <article className="console-summary" key={index}>{typeof summary === "string" ? summary : summary.text || "Summary unavailable"}</article>) : <p className="muted">此 provider/model 未回傳 reasoning summary；不顯示原始 chain-of-thought。</p>}</section>
    <section><p className="eyebrow">TOOL CARDS</p><div className="console-tools">{tools.map(tool => <article key={`${tool.sequence}-${tool.kind}`}><b>{tool.message}</b><small>{tool.kind} · {tool.step} · #{tool.sequence}</small><code>{JSON.stringify(tool.payload, null, 2)}</code></article>)}</div></section>
    <section><p className="eyebrow">CHECKPOINTS</p>{checkpoints.length ? <div className="checkpoint-list">{checkpoints.map((checkpoint, index) => <code key={`${checkpoint}-${index}`}>#{index + 1} {checkpoint}</code>)}</div> : <p className="muted">此 run 沒有 checkpoint。</p>}</section>
    <section><p className="eyebrow">OFFICIAL SOURCES</p>{sources.length ? <div className="source-list">{sources.map((source, index) => <article key={source.id || index}><b>{source.publisher || source.id || "Source"}</b><span>{source.document_type} · parser {source.parser_version || "n/a"}</span>{source.url && <a href={source.url} target="_blank" rel="noreferrer">{source.url}</a>}<code>{source.sha256 || "hash unavailable"} · {source.object_key || "object reference unavailable"}</code></article>)}</div> : <p className="muted">此 execution 沒有直接來源文件。</p>}</section>
    <section><div className="trace-filter"><p className="eyebrow">EVENTS</p><select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">All events</option><option value="step">Steps</option><option value="tool">Tools</option><option value="reasoning">Reasoning</option><option value="run">Terminal / errors</option></select></div><div className="trace-events">{visible.map(event => <div key={`${event.sequence}-${event.kind}`}><time>#{event.sequence}</time><b>{event.kind}</b><span>{event.message}</span></div>)}</div></section>
    <details><summary>Immutable config snapshot</summary><pre className="trace-json">{JSON.stringify(config, null, 2)}</pre></details>
  </div>;
}

export default function ConsolePage() {
  const [authenticated, setAuthenticated] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [token, setToken] = useState("");
  const [tab, setTab] = useState("runs");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [runs, setRuns] = useState<Execution[]>([]);
  const [universes, setUniverses] = useState<Universe[]>([]);
  const [registry, setRegistry] = useState<Record<string, unknown>>({});
  const [audits, setAudits] = useState<Audit[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [editor, setEditor] = useState("");
  const [draft, setDraft] = useState<Profile | null>(null);
  const [trace, setTrace] = useState<Record<string, unknown> | null>(null);
  const [diff, setDiff] = useState("");
  const [notice, setNotice] = useState("");
  const selected = profiles.find(profile => profile.id === selectedProfile);
  const grouped = useMemo(() => Object.entries(Object.groupBy(profiles, profile => profile.poc_type)), [profiles]);

  async function loadAll() {
    const [profileData, runData, universeData, registryData, auditData] = await Promise.all([
      api("/api/v1/admin/profiles"), api("/api/v1/admin/executions"), api("/api/v1/admin/universe"), api("/api/v1/admin/registry"), api("/api/v1/admin/audit-log"),
    ]);
    setProfiles(profileData); setRuns(runData); setUniverses(universeData); setRegistry(registryData); setAudits(auditData);
    if (!selectedProfile && profileData[0]) setSelectedProfile(profileData[0].id);
  }
  useEffect(() => { api("/api/v1/admin/status").then(status => { setConfigured(status.configured); setAuthenticated(status.authenticated); if (status.authenticated) loadAll(); }); }, []);
  useEffect(() => { if (selected) setEditor(JSON.stringify(selected.config, null, 2)); }, [selectedProfile, profiles.length]);
  useEffect(() => {
    if (!authenticated) return;
    const params = new URLSearchParams(window.location.search), type = params.get("type"), run = params.get("run");
    if (type && run) openTrace(type, run);
  }, [authenticated]);

  async function login(event: React.FormEvent) {
    event.preventDefault(); setNotice("");
    try { await api("/api/v1/admin/session", { method: "POST", body: JSON.stringify({ token }) }); setAuthenticated(true); await loadAll(); }
    catch (cause) { setNotice((cause as Error).message); }
  }
  async function logout() { await api("/api/v1/admin/session", { method: "DELETE" }); setAuthenticated(false); }
  async function makeDraft() {
    if (!selected) return;
    try { const config = JSON.parse(editor), created = await api(`/api/v1/admin/profiles/${selected.poc_type}/drafts`, { method: "POST", body: JSON.stringify({ name: selected.name, config }) }); setDraft(created); setNotice(`Draft v${created.version} 已建立`); await loadAll(); }
    catch (cause) { setNotice(`Draft 失敗：${(cause as Error).message}`); }
  }
  async function validate() { if (!draft) return; try { const result = await api(`/api/v1/admin/profiles/${draft.id}/validate`, { method: "POST" }); setNotice(result.valid ? "DAG、tools、skills 與 budget 驗證通過" : result.errors.join("；")); await loadAll(); } catch (cause) { setNotice((cause as Error).message); } }
  async function publish() { if (!draft) return; try { await api(`/api/v1/admin/profiles/${draft.id}/publish`, { method: "POST" }); setNotice(`v${draft.version} 已發布；只影響新 run`); setDraft(null); await loadAll(); } catch (cause) { setNotice((cause as Error).message); } }
  async function rollback(profile: Profile) { try { const result = await api(`/api/v1/admin/profiles/${profile.id}/rollback`, { method: "POST" }); setNotice(`已建立並發布 rollback v${result.version}`); await loadAll(); } catch (cause) { setNotice((cause as Error).message); } }
  async function compareVersion() { if (!selected) return; const previous = profiles.find(profile => profile.poc_type === selected.poc_type && profile.version === selected.version - 1); if (!previous) { setNotice("沒有前一個版本可比較"); return; } try { const result = await api(`/api/v1/admin/profiles/${previous.id}/diff?other_id=${selected.id}`); setDiff(result.diff); } catch (cause) { setNotice((cause as Error).message); } }
  async function syncUniverse() { setNotice("同步官方 universe…"); try { const result = await api("/api/v1/admin/universe/sync", { method: "POST" }); setNotice(JSON.stringify(result)); await loadAll(); } catch (cause) { setNotice((cause as Error).message); } }
  async function openTrace(type: string, id: string) { try { setTrace(await api(`/api/v1/executions/${type}/${id}/trace`)); setTab("runs"); } catch (cause) { setNotice((cause as Error).message); } }

  if (!authenticated) return <main className="console"><header><div className="brand">SIGNAL<span>FORGE</span></div><div className="badge">DEVELOPER CONSOLE</div></header><section className="console-login"><p className="eyebrow">ADMIN SESSION</p><h1>Agent 控制台</h1><p>登入後可查看完整 trace、發布 Agent profile 與同步 universe。秘密只從環境變數讀取，不會由 API 回傳。</p>{!configured && <div className="console-warning">請先設定 ADMIN_TOKEN 與 ADMIN_SESSION_SECRET。</div>}<form onSubmit={login}><input type="password" value={token} onChange={event => setToken(event.target.value)} placeholder="Admin token" autoComplete="current-password" /><button disabled={!configured}>建立安全 session</button></form>{notice && <p className="form-error">{notice}</p>}</section></main>;

  return <main className="console"><header><div className="brand">SIGNAL<span>FORGE</span></div><div className="badge">DEVELOPER CONSOLE</div><button className="console-logout" onClick={logout}>LOG OUT</button></header>
    <section className="console-shell"><aside className="console-sidebar"><p className="eyebrow">OPERATIONS</p>{[["runs", "Runs"], ["profiles", "Agent Profiles"], ["models", "Models"], ["capabilities", "Tools & Skills"], ["universe", "Universe"], ["audit", "Audit Log"]].map(([id, label]) => <button className={tab === id ? "active" : ""} key={id} onClick={() => setTab(id)}>{label}</button>)}</aside>
      <article className="console-content"><div className="console-title"><div><p className="eyebrow">ADMIN · {tab.toUpperCase()}</p><h1>{tab === "profiles" ? "Agent 設定版本" : tab === "runs" ? "Execution Runs" : tab}</h1></div><span>SESSION ACTIVE</span></div>{notice && <div className="console-notice">{notice}</div>}
        {tab === "runs" && <div className="console-two"><div className="console-table">{runs.map(run => <button key={`${run.type}-${run.id}`} onClick={() => openTrace(run.type, run.id)}><b>{run.ticker || run.type}</b><span>{run.type} · {run.status}</span><small>{new Date(run.created_at).toLocaleString()}</small></button>)}</div>{trace ? <TraceDetail trace={trace} /> : <pre className="trace-json">選擇 run 查看 events、DAG、reasoning summaries、tools、checkpoints、usage 與不可變 config snapshot。</pre>}</div>}
        {tab === "profiles" && <><div className="profile-work"><div className="profile-list">{grouped.map(([type, versions]) => <section key={type}><h3>{type}</h3>{versions?.map(profile => <button className={selectedProfile === profile.id ? "active" : ""} key={profile.id} onClick={() => { setSelectedProfile(profile.id); setDiff(""); }}><b>v{profile.version}</b><span>{profile.status}</span>{profile.status === "archived" && <em onClick={event => { event.stopPropagation(); rollback(profile); }}>rollback</em>}</button>)}</section>)}</div><div className="profile-editor"><div><b>{selected?.name}</b><span>已發布設定對既有 run 不產生漂移</span></div><textarea value={editor} onChange={event => setEditor(event.target.value)} spellCheck={false} /><footer><button onClick={compareVersion}>Diff previous</button><button onClick={makeDraft}>建立 Draft</button><button onClick={validate} disabled={!draft}>Validate</button><button onClick={publish} disabled={!draft}>Publish</button></footer></div></div>{diff && <pre className="registry-json">{diff}</pre>}</>}
        {tab === "models" && <div className="registry-grid">{((registry.models || []) as { provider: string; model: string; configured: boolean }[]).map(item => <section key={`${item.provider}-${item.model}`}><p className="eyebrow">{item.provider}</p><h2>{item.model}</h2><span className={item.configured ? "configured" : "unconfigured"}>{item.configured ? "CONFIGURED" : "NOT CONFIGURED"}</span></section>)}</div>}
        {tab === "capabilities" && <div className="capability-registry"><section><p className="eyebrow">REGISTERED TOOLS</p><div className="registry-chips">{((registry.tools || []) as string[]).map(tool => <code key={tool}>{tool}</code>)}</div></section><section><p className="eyebrow">VERSIONED SKILLS</p><div className="registry-chips">{((registry.skills || []) as string[]).map(skill => <code key={skill}>{skill}</code>)}</div></section><section><p className="eyebrow">PIPELINE COMPONENTS</p>{Object.entries((registry.components || {}) as Record<string, string[]>).map(([poc, components]) => <div className="component-row" key={poc}><b>{poc}</b><span>{components.join(" → ")}</span></div>)}</section></div>}
        {tab === "universe" && <><button className="console-primary" onClick={syncUniverse}>立即同步官方名單</button><div className="universe-grid">{universes.map(item => <section key={item.id}><p className="eyebrow">{item.universe} · {item.source_status}</p><h2>{item.member_count} securities / {item.issuer_count} issuers</h2>{item.sync_error && <div className="trace-error"><b>LAST SYNC FAILED</b><span>{item.sync_error}</span></div>}<dl><dt>Version</dt><dd>{item.version}</dd><dt>As of</dt><dd>{item.as_of_date}</dd><dt>Artifacts</dt><dd>{item.artifacts.length}</dd><dt>Hash</dt><dd>{item.content_hash}</dd><dt>Source</dt><dd><a href={item.source_url} target="_blank" rel="noreferrer">official source ↗</a></dd></dl></section>)}</div></>}
        {tab === "audit" && <div className="audit-list">{audits.map(item => <div key={item.id}><time>{new Date(item.created_at).toLocaleString()}</time><b>{item.action}</b><span>{item.target_type} · {item.target_id}</span><code>{JSON.stringify(item.detail)}</code></div>)}</div>}
      </article></section>
  </main>;
}
