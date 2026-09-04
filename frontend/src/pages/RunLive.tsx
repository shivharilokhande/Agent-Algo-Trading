import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { api, getToken, RunEventMsg, RunOut, runStreamUrl } from "../api";

const TEAMS: [string, string[]][] = [
  ["Analyst Team", ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"]],
  ["Research Team", ["Bull Researcher", "Bear Researcher", "Research Manager"]],
  ["Trading", ["Trader"]],
  ["Risk Management", ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"]],
  ["Portfolio", ["Portfolio Manager"]],
];
const SECTION_ORDER = [
  "market_report", "sentiment_report", "news_report", "fundamentals_report",
  "custom_report", "investment_plan", "trader_investment_plan", "final_trade_decision",
];
const SECTION_TITLES: Record<string, string> = {
  market_report: "Market", sentiment_report: "Sentiment", news_report: "News",
  fundamentals_report: "Fundamentals", custom_report: "Custom Analysts",
  investment_plan: "Research Decision",
  trader_investment_plan: "Trader Plan", final_trade_decision: "Final Decision",
};

const ASKABLE = ["Bull Researcher", "Bear Researcher", "Trader", "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"];

function HitlPanel({ runId }: { runId: string }) {
  const [agent, setAgent] = useState(ASKABLE[1]);
  const [question, setQuestion] = useState("");
  const [view, setView] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  async function ask() {
    setBusy("ask"); setErr("");
    try {
      await api.post(`/api/runs/${runId}/ask`, { agent, question: question.trim() });
      setQuestion("");  // the exchange arrives through the live feed
    } catch (ex: any) { setErr(ex.message); } finally { setBusy(""); }
  }
  async function proceed() {
    setBusy("go"); setErr("");
    try {
      await api.post(`/api/runs/${runId}/proceed`, { user_view: view.trim() });
    } catch (ex: any) { setErr(ex.message); setBusy(""); }
  }

  return (
    <div className="card" style={{ borderColor: "var(--blue)", boxShadow: "0 0 0 1px var(--blue)" }}>
      <h3>⏸ Decision breakpoint — the Portfolio Manager is waiting for you</h3>
      <p className="muted">Interrogate any agent about their reasoning (answers appear in the live feed), then optionally state your own view — the final decision must address it.</p>
      {err && <div className="error-box">{err}</div>}
      <div className="row" style={{ marginBottom: 10 }}>
        <select value={agent} onChange={(e) => setAgent(e.target.value)} style={{ width: 190 }}>
          {ASKABLE.map((a) => <option key={a}>{a}</option>)}
        </select>
        <input placeholder="e.g. What breaks your thesis?" value={question} style={{ flex: 1, minWidth: 220 }}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && question.trim() && ask()} />
        <button className="secondary" disabled={!question.trim() || busy === "ask"} onClick={ask}>
          {busy === "ask" ? "…" : "Ask"}
        </button>
      </div>
      <textarea rows={2} placeholder="Your view (optional) — e.g. “I already hold a position; I want a tighter stop.”"
        value={view} onChange={(e) => setView(e.target.value)} style={{ marginBottom: 10 }} />
      <button disabled={busy === "go"} onClick={proceed}>
        {busy === "go" ? "Resuming…" : "▶ Proceed to final decision"}
      </button>
    </div>
  );
}

export default function RunLive() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<RunOut | null>(null);
  const [agentStatus, setAgentStatus] = useState<Record<string, string>>({});
  const [feed, setFeed] = useState<RunEventMsg[]>([]);
  const [sections, setSections] = useState<Record<string, string>>({});
  const [stats, setStats] = useState<Record<string, number>>({});
  const [finalInfo, setFinalInfo] = useState<any>(null);
  const [tab, setTab] = useState("feed");
  const [wsState, setWsState] = useState("connecting");
  const feedRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  async function refreshRun() {
    if (id) setRun(await api.get<RunOut>(`/api/runs/${id}`));
  }

  useEffect(() => {
    if (!id || !getToken()) return;
    refreshRun();
    let closedByUs = false;
    let ended = false;
    async function connect() {
      let url: string;
      try {
        url = await runStreamUrl(id!);  // fresh ticket per (re)connect
      } catch {
        setWsState("auth-failed");
        return;
      }
      if (closedByUs) return;
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => setWsState("live");
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "stream_end") { ended = true; setWsState("ended"); refreshRun(); ws.close(); return; }
        handleEvent(msg);
      };
      ws.onclose = () => {
        if (!closedByUs && !ended) {
          setWsState("reconnecting");
          setTimeout(() => { if (!closedByUs && !ended) connect(); }, 2000);
        }
      };
    }
    connect();
    // P2: a queued run has no live stream yet — poll until it starts, then reconnect
    const queuedPoll = setInterval(async () => {
      if (closedByUs || ended) { clearInterval(queuedPoll); return; }
      try {
        const r = await api.get<RunOut>(`/api/runs/${id}`);
        setRun(r);
        if (r.status === "running" && wsRef.current?.readyState !== WebSocket.OPEN) connect();
        if (["done", "failed", "cancelled", "interrupted"].includes(r.status)) clearInterval(queuedPoll);
      } catch { /* ignore */ }
    }, 3000);
    return () => { closedByUs = true; clearInterval(queuedPoll); wsRef.current?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function handleEvent(msg: RunEventMsg) {
    switch (msg.type) {
      case "agent_status":
        setAgentStatus((s) => ({ ...s, [msg.agent]: msg.payload.status }));
        break;
      case "message":
      case "tool_call":
        setFeed((f) => (f.length > 400 ? [...f.slice(-350), msg] : [...f, msg]));
        break;
      case "report_section":
        setSections((s) => ({ ...s, [msg.payload.section]: msg.payload.content_md }));
        break;
      case "stats":
        setStats(msg.payload);
        break;
      case "run_status":
        if (["done", "failed", "cancelled", "interrupted", "paused", "running"].includes(msg.payload.status)) {
          if (["done", "failed", "cancelled", "interrupted"].includes(msg.payload.status)) setFinalInfo(msg.payload);
          refreshRun();
        }
        break;
    }
  }

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
  }, [feed]);

  async function cancel() {
    await api.post(`/api/runs/${id}/cancel`).catch(() => {});
    refreshRun();
  }
  async function resume() {
    setFeed([]); setSections({}); setAgentStatus({});
    await api.post(`/api/runs/${id}/resume`);
    window.location.reload();
  }

  if (!run) return <p className="muted">Loading…</p>;
  const doneAgents = Object.values(agentStatus).filter((s) => s === "done").length;
  const progress = run.status === "done" ? 100 : Math.round((doneAgents / 12) * 100);
  const sectionKeys = SECTION_ORDER.filter((s) => sections[s]);

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>
          {run.ticker} <span className="muted" style={{ fontSize: 16 }}>· {run.trade_date} · {run.mode} mode · benchmark {run.benchmark}</span>
        </h1>
        <div className="row">
          <span className={`pill ${run.status}`}>{run.status}</span>
          {run.rating && <span className={`rating ${run.rating}`}>{run.rating}</span>}
        </div>
      </div>

      <div className="progressbar"><div style={{ width: `${progress}%` }} /></div>

      <div className="row" style={{ marginBottom: 14 }}>
        {["running", "queued"].includes(run.status) && <button className="danger small" onClick={cancel}>■ Cancel</button>}
        {["interrupted", "cancelled"].includes(run.status) && <button className="small" onClick={resume}>↻ Resume from checkpoint</button>}
        {run.status === "done" && (
          <a href="#" onClick={async (e) => {
            e.preventDefault();
            const md = await api.get<string>(`/api/runs/${run.id}/report.md`);
            const blob = new Blob([md], { type: "text/markdown" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `${run.ticker}_${run.trade_date}_report.md`;
            a.click();
          }}>⬇ Export report.md</a>
        )}
        <span className="muted">stream: {wsState}</span>
      </div>

      {run.status === "paused" && <HitlPanel runId={run.id} />}

      {run.error && <div className="error-box">{run.error}</div>}
      {run.rating === "REVIEW" && (
        <div className="error-box">
          The decision had no parseable rating (REVIEW) — re-run rather than treating this as Hold.
        </div>
      )}

      {/* Agent pipeline board */}
      <div className="card">
        {TEAMS.map(([team, agents]) => (
          <div key={team}>
            <div className="team-label">{team}</div>
            <div className="agent-grid">
              {agents.map((a) => {
                const st = run.status === "done" ? "done" : agentStatus[a] || "pending";
                return (
                  <div key={a} className={`agent-cell ${st}`}>
                    <span>{a}</span>
                    <span className={`pill ${st}`}>{st === "in_progress" ? "working" : st}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Stats */}
      <div className="card">
        <div className="grid4">
          <div className="stat"><div className="v">{stats.llm_calls ?? run.stats.llm_calls ?? 0}</div><div className="l">LLM calls</div></div>
          <div className="stat"><div className="v">{stats.tool_calls ?? run.stats.tool_calls ?? 0}</div><div className="l">Tool calls</div></div>
          <div className="stat"><div className="v">{((stats.tokens_in ?? run.stats.tokens_in ?? 0) / 1000).toFixed(1)}k</div><div className="l">Tokens in</div></div>
          <div className="stat"><div className="v">{((stats.tokens_out ?? run.stats.tokens_out ?? 0) / 1000).toFixed(1)}k</div><div className="l">Tokens out</div></div>
        </div>
      </div>

      {/* Feed + report tabs */}
      <div className="tabs">
        <div className={`tab ${tab === "feed" ? "active" : ""}`} onClick={() => setTab("feed")}>
          Live feed ({feed.length})
        </div>
        {sectionKeys.map((s) => (
          <div key={s} className={`tab ${tab === s ? "active" : ""}`} onClick={() => setTab(s)}>
            {SECTION_TITLES[s]}
          </div>
        ))}
      </div>

      {tab === "feed" ? (
        <div className="feed" ref={feedRef}>
          {feed.map((m) => (
            <div key={m.seq} className={`feed-item ${m.type === "tool_call" ? "tool" : ""} ${m.payload.kind === "system" ? "system" : ""}`}>
              {m.type === "tool_call" ? (
                <><span className="who">⚙ {m.payload.tool}</span>
                  <span className="txt mono">{JSON.stringify(m.payload.args).slice(0, 120)}</span></>
              ) : (
                <><span className="who">{m.agent || m.payload.kind || "engine"}</span>
                  <span className="txt">{(m.payload.text || "").slice(0, 600)}</span></>
              )}
            </div>
          ))}
          {feed.length === 0 && <p className="muted">Waiting for events…</p>}
        </div>
      ) : (
        <div className="report" dangerouslySetInnerHTML={{
          __html: DOMPurify.sanitize(marked.parse(sections[tab] || "") as string),
        }} />
      )}

      {finalInfo?.status === "done" && (
        <div className="ok-box" style={{ marginTop: 14 }}>
          Analysis complete — {finalInfo.rating}. {finalInfo.decision_summary}
          {" "}A pending entry was added to your decision log (Memory page).
        </div>
      )}
    </div>
  );
}
