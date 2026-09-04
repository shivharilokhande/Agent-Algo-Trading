import { useEffect, useState } from "react";
import { api } from "../api";

export default function Research() {
  const [docs, setDocs] = useState<any[]>([]);
  const [hits, setHits] = useState<any[] | null>(null);
  const [q, setQ] = useState("");
  const [ticker, setTicker] = useState("");
  const [ingestTicker, setIngestTicker] = useState("AAPL");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState("");

  async function load() { setDocs(await api.get("/api/library")); }
  useEffect(() => { load(); }, []);

  async function ingest() {
    setBusy("ingest"); setMsg(null);
    try {
      const r = await api.post<any>(`/api/library/ingest/${ingestTicker.trim()}`);
      setMsg({
        kind: "ok",
        text: r.count > 0
          ? `Ingested ${r.count} SEC filing(s): ${r.ingested.map((f: any) => `${f.form} ${f.filed}`).join(", ")}`
          : "No new filings (already ingested, or none available).",
      });
      await load();
    } catch (ex: any) { setMsg({ kind: "error", text: ex.message }); }
    finally { setBusy(""); }
  }

  async function search() {
    setBusy("search"); setMsg(null);
    try {
      const params = new URLSearchParams({ q: q.trim() });
      if (ticker.trim()) params.set("ticker", ticker.trim());
      setHits(await api.get(`/api/library/search?${params}`));
    } catch (ex: any) { setMsg({ kind: "error", text: ex.message }); }
    finally { setBusy(""); }
  }

  return (
    <div>
      <h1>Research Library</h1>
      <p className="page-sub">SEC filings and your own reports, full-text indexed. Analyses cite matching passages ("Grounded in the research library") with point-in-time filtering.</p>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="card">
        <h3>Ingest SEC filings (EDGAR)</h3>
        <div className="row">
          <input value={ingestTicker} onChange={(e) => setIngestTicker(e.target.value.toUpperCase())}
            style={{ width: 140 }} placeholder="US ticker" />
          <button disabled={busy === "ingest"} onClick={ingest}>
            {busy === "ingest" ? "Fetching from EDGAR…" : "Ingest latest 10-K / 10-Q"}
          </button>
          <span className="muted">US listings only · free SEC EDGAR API</span>
        </div>
      </div>

      <div className="card">
        <h3>Search</h3>
        <div className="row">
          <input value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 240 }}
            placeholder='e.g. "supply chain" risk revenue' onKeyDown={(e) => e.key === "Enter" && q.trim() && search()} />
          <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())}
            style={{ width: 120 }} placeholder="Ticker (opt.)" />
          <button className="secondary" disabled={!q.trim() || busy === "search"} onClick={search}>Search</button>
        </div>
        {hits !== null && (
          <div style={{ marginTop: 12 }}>
            {hits.length === 0 && <p className="muted">No matches.</p>}
            {hits.map((h) => (
              <div key={h.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
                <b>{h.title}</b> <span className={`pill ${h.source === "sec_filing" ? "running" : "pending"}`}>{h.source.replace("_", " ")}</span>
                <div className="muted" style={{ marginTop: 4 }}
                  dangerouslySetInnerHTML={{ __html: h.snippet.replace(/«/g, "<mark>").replace(/»/g, "</mark>") }} />
                {h.url.startsWith("http") && <a href={h.url} target="_blank" rel="noreferrer">source ↗</a>}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Library ({docs.length})</h3>
        <table>
          <thead><tr><th>Title</th><th>Ticker</th><th>Source</th><th>Date</th><th>Size</th></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td><b>{d.title}</b></td>
                <td>{d.ticker}</td>
                <td className="muted">{d.source.replace("_", " ")}</td>
                <td className="muted">{d.doc_date}</td>
                <td className="muted">{(d.chars / 1000).toFixed(0)}k chars</td>
              </tr>
            ))}
            {docs.length === 0 && <tr><td colSpan={5} className="muted">
              Empty — ingest SEC filings above, or index a finished run from its report page.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
