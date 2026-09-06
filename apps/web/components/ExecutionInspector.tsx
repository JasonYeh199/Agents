"use client";

import { useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");
export type ExecutionEvent = { sequence: number; kind: string; step?: string; message: string; timestamp?: string; payload?: Record<string, unknown> };

const EVENT_KINDS = ["step.started", "step.completed", "step.retry", "step.recovered", "tool.started", "tool.completed", "decision.summary", "reasoning.summary.delta", "reasoning.summary.unavailable", "checkpoint", "run.completed", "run.failed"];

export default function ExecutionInspector({ type, id, onTerminal }: { type: string; id?: string; onTerminal?: (status: string) => void }) {
  const [events, setEvents] = useState<ExecutionEvent[]>([]);
  const [connection, setConnection] = useState<"idle" | "streaming" | "reconnecting" | "completed" | "failed">("idle");
  useEffect(() => {
    setEvents([]);
    setConnection("idle");
    if (!id) return;
    let terminal = false;
    const stream = new EventSource(`${API}/api/v1/executions/${type}/${id}/events`);
    const receive = (raw: Event) => {
      const message = raw as MessageEvent;
      try { const item = JSON.parse(message.data); if (item.sequence) setEvents(current => current.some(x => x.sequence === item.sequence) ? current : [...current, item]); } catch { /* heartbeat/terminal */ }
    };
    EVENT_KINDS.forEach(kind => stream.addEventListener(kind, receive));
    stream.onmessage = receive;
    stream.addEventListener("complete", event => { receive(event); terminal = true; setConnection("completed"); onTerminal?.("completed"); stream.close(); });
    stream.addEventListener("failed", event => { receive(event); terminal = true; setConnection("failed"); onTerminal?.("failed"); stream.close(); });
    stream.onopen = () => setConnection("streaming");
    stream.onerror = () => { if (!terminal) setConnection("reconnecting"); };
    return () => stream.close();
  }, [type, id]);
  const steps = useMemo(() => {
    const map = new Map<string, string>();
    const snapshot = events.find(event => event.kind === "pipeline.snapshot");
    const pipeline = (snapshot?.payload?.pipeline || []) as { id: string; enabled?: boolean }[];
    pipeline.filter(node => node.enabled !== false).forEach(node => map.set(node.id, "pending"));
    events.forEach(event => {
      if (!event.step) return;
      if (event.kind.startsWith("step.")) map.set(event.step, event.kind.split(".")[1]);
      if (event.kind === "capability.started" || event.kind === "variant.started" || event.kind === "agent.started") map.set(event.step, "started");
      if (event.kind === "checkpoint.saved" || event.kind === "variant.completed") map.set(event.step, "completed");
    });
    return [...map.entries()];
  }, [events]);
  const reasoning = events.filter(event => event.kind.includes("summary"));
  const tools = events.filter(event => event.kind.startsWith("tool."));
  const checkpoints = events.filter(event => event.kind.startsWith("checkpoint."));
  const failures = events.filter(event => event.kind.endsWith("failed") || event.kind.includes("retry"));
  const snapshot = events.find(event => event.kind === "pipeline.snapshot")?.payload;
  const connectionLabel = {
    idle: "○ IDLE", streaming: "● STREAMING", reconnecting: "◌ RECONNECTING", completed: "✓ COMPLETED", failed: "× FAILED",
  }[connection];
  return <section className="execution-inspector">
    <div className="inspector-head"><div><p className="eyebrow">LIVE EXECUTION INSPECTOR</p><h2>Agent 執行工作台</h2></div><span className={connection === "streaming" ? "live" : connection}>{connectionLabel}</span></div>
    {!id ? <p className="inspector-empty">開始研究後，這裡會即時顯示 Agent、工具、reasoning summary 與 checkpoint。</p> : <>
      {snapshot && <div className="inspector-runtime"><span>{String(snapshot.provider || "deterministic")}</span><span>{String(snapshot.model || "template-v1")}</span><span>reasoning {String(snapshot.reasoning_effort || "n/a")}</span><span>prompt {String(snapshot.prompt_version || "n/a")}</span><span>profile {String(snapshot.profile_version_id || "n/a").slice(0, 8)}</span></div>}
      <div className="dag">{steps.map(([step, status], index) => <div key={step}><i className={status} />{index > 0 && <b>→</b>}<span>{step}<small>{status}</small></span></div>)}</div>
      <div className="inspector-columns">
        <div><h3>Reasoning / decision summary</h3>{reasoning.length ? reasoning.map(event => <article key={event.sequence} className="reason-card"><b>{event.step || "agent"}</b><p>{event.message}</p></article>) : <p className="muted">等待支援的 summary event；不顯示未公開 chain-of-thought。</p>}</div>
        <div><h3>Tools & checkpoints</h3>{[...tools, ...checkpoints].sort((a, b) => a.sequence - b.sequence).map(event => <article key={event.sequence} className={event.kind.startsWith("checkpoint") ? "checkpoint-card" : "tool-card"}><b>{event.message}</b><small>{event.kind} · {event.step} · #{event.sequence}</small>{event.payload && <code>{JSON.stringify(event.payload)}</code>}</article>)}</div>
      </div>
      {!!failures.length && <div className="inspector-errors">{failures.map(event => <p key={event.sequence}><b>{event.kind}</b> {event.message}</p>)}</div>}
      <div className="event-log">{events.map(event => <div key={event.sequence}><time>#{event.sequence}</time><b>{event.kind}</b><span>{event.message}</span></div>)}</div>
      <a className="console-link" href={`/console?type=${type}&run=${id}`}>在 Console 查看完整 trace →</a>
    </>}
  </section>;
}
