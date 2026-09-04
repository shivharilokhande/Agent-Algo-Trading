import { useEffect, useState } from "react";
import { api } from "../api";

export default function Studio() {
  const [state, setState] = useState<any>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [editAgent, setEditAgent] = useState("");
  const [persona, setPersona] = useState("");
  // custom analyst form
  const [cName, setCName] = useState("");
  const [cPersona, setCPersona] = useState("");
  const [cTools, setCTools] = useState<string[]>([]);

  async function load() { setState(await api.get("/api/studio")); }
  useEffect(() => { load(); }, []);

  const profileFor = (key: string) => state?.profiles.find((p: any) => p.agent_key === key);

  async function saveStock() {
    setMsg(null);
    try {
      await api.post("/api/studio", { agent_key: editAgent, persona });
      setMsg({ kind: "ok", text: `${editAgent} persona saved — applied to every new run.` });
      setEditAgent(""); setPersona("");
      await load();
    } catch (ex: any) { setMsg({ kind: "error", text: ex.message }); }
  }

  async function saveCustom() {
    setMsg(null);
    try {
      await api.post("/api/studio", {
        agent_key: `custom:${cName.trim()}`, display_name: cName.trim(),
        persona: cPersona, tools: cTools,
      });
      setMsg({ kind: "ok", text: `Custom analyst "${cName}" added — it joins the analyst stage of every new run.` });
      setCName(""); setCPersona(""); setCTools([]);
      await load();
    } catch (ex: any) { setMsg({ kind: "error", text: ex.message }); }
  }

  if (!state) return <p className="muted">Loading…</p>;
  const customs = state.profiles.filter((p: any) => p.agent_key.startsWith("custom:"));

  return (
    <div>
      <h1>Agent Studio</h1>
      <p className="page-sub">Customize how the agent team thinks: override any agent's persona, or add your own analysts to the pipeline.</p>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="card">
        <h3>Stock agents</h3>
        <table>
          <thead><tr><th>Agent</th><th>Persona override</th><th></th></tr></thead>
          <tbody>
            {state.stock_agents.map((a: string) => {
              const p = profileFor(a);
              return (
                <tr key={a}>
                  <td><b>{a}</b></td>
                  <td className="muted" style={{ maxWidth: 420 }}>
                    {p?.persona ? p.persona.slice(0, 140) : <i>engine default</i>}
                  </td>
                  <td className="row">
                    <button className="secondary small"
                      onClick={() => { setEditAgent(a); setPersona(p?.persona || ""); }}>
                      {p ? "Edit" : "Override"}
                    </button>
                    {p && <button className="danger small"
                      onClick={async () => { await api.del(`/api/studio/${p.id}`); load(); }}>Reset</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {editAgent && (
          <div style={{ marginTop: 12 }}>
            <label>Persona for {editAgent}</label>
            <textarea rows={3} value={persona} onChange={(e) => setPersona(e.target.value)}
              placeholder="e.g. You are a mean-reversion specialist; distrust momentum and demand confirmation volume." />
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={saveStock} disabled={!persona.trim()}>Save persona</button>
              <button className="secondary" onClick={() => setEditAgent("")}>Cancel</button>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <h3>Custom analysts</h3>
        {customs.length > 0 && (
          <table>
            <thead><tr><th>Name</th><th>Persona</th><th>Tools</th><th></th></tr></thead>
            <tbody>
              {customs.map((p: any) => (
                <tr key={p.id}>
                  <td><b>{p.display_name}</b></td>
                  <td className="muted" style={{ maxWidth: 360 }}>{p.persona.slice(0, 120)}</td>
                  <td className="mono" style={{ fontSize: 11 }}>{p.tools.join(", ")}</td>
                  <td><button className="danger small"
                    onClick={async () => { await api.del(`/api/studio/${p.id}`); load(); }}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div style={{ marginTop: 12 }}>
          <div className="grid2">
            <div>
              <label>Analyst name</label>
              <input value={cName} onChange={(e) => setCName(e.target.value)} placeholder="e.g. ESG Screener" />
            </div>
            <div>
              <label>Tools (max 6)</label>
              <div className="row">
                {state.toolbox.map((t: string) => (
                  <label key={t} className="checkbox-row" style={{ color: "var(--text)", fontSize: 12 }}>
                    <input type="checkbox" checked={cTools.includes(t)}
                      onChange={() => setCTools((x) => x.includes(t) ? x.filter((y) => y !== t) : [...x, t].slice(0, 6))} />
                    {t}
                  </label>
                ))}
              </div>
            </div>
          </div>
          <label style={{ marginTop: 8 }}>Persona / mandate</label>
          <textarea rows={2} value={cPersona} onChange={(e) => setCPersona(e.target.value)}
            placeholder="e.g. Evaluate governance and regulatory risk only; ignore valuation." />
          <div style={{ marginTop: 8 }}>
            <button onClick={saveCustom} disabled={!cName.trim() || !cPersona.trim()}>Add custom analyst</button>
          </div>
        </div>
      </div>
      <p className="muted">Personas and custom analysts apply to demo runs today; engine-mode prompt overrides are on the roadmap.</p>
    </div>
  );
}
