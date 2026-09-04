import { useEffect, useState } from "react";
import { api, KeyOut, ProviderInfo } from "../api";

// C11 — provider-specific extra credential fields (stored in the vault's `extra`)
const EXTRA_FIELDS: Record<string, { key: string; label: string; ph: string; secret?: boolean }[]> = {
  ollama: [{ key: "base_url", label: "Base URL", ph: "http://localhost:11434/v1" }],
  openai_compatible: [{ key: "base_url", label: "Base URL", ph: "http://localhost:8000/v1 (vLLM) or :1234/v1 (LM Studio)" }],
  azure: [
    { key: "endpoint", label: "Azure endpoint", ph: "https://myresource.openai.azure.com" },
    { key: "api_version", label: "API version", ph: "2024-06-01" },
  ],
  bedrock: [
    { key: "secret_access_key", label: "AWS secret access key", ph: "", secret: true },
    { key: "region", label: "AWS region", ph: "us-east-1" },
  ],
};

export default function Settings() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [dataProviders, setDataProviders] = useState<ProviderInfo[]>([]);
  const [keys, setKeys] = useState<KeyOut[]>([]);
  const [editing, setEditing] = useState<string>("");
  const [secret, setSecret] = useState("");
  const [extraForm, setExtraForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [defaults, setDefaults] = useState<any>({});
  const [presets, setPresets] = useState<any[]>([]);

  async function load() {
    const p = await api.get<any>("/api/catalog/providers");
    setProviders(p.llm_providers);
    setDataProviders(p.data_providers);
    setKeys(await api.get<KeyOut[]>("/api/keys"));
    setDefaults((await api.get<{ config: any }>("/api/settings")).config || {});
    setPresets(await api.get<any[]>("/api/presets"));
  }
  useEffect(() => { load(); }, []);

  const keyFor = (id: string) => keys.find((k) => k.provider === id);

  async function save(p: ProviderInfo) {
    setBusy(p.id); setMsg(null);
    try {
      const extra: Record<string, string> = {};
      for (const f of EXTRA_FIELDS[p.id] ?? []) {
        if (extraForm[f.key]?.trim()) extra[f.key] = extraForm[f.key].trim();
      }
      await api.put("/api/keys", { provider: p.id, secret, extra });
      setMsg({ kind: "ok", text: `${p.name} credential saved (encrypted at rest).` });
      setEditing(""); setSecret(""); setExtraForm({});
      await load();
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    } finally { setBusy(""); }
  }

  async function test(id: string) {
    setBusy(id); setMsg(null);
    try {
      const r = await api.post<KeyOut>(`/api/keys/${id}/test`);
      setMsg(r.status === "valid"
        ? { kind: "ok", text: `${id}: key is valid ✓` }
        : { kind: "error", text: `${id}: validation failed (${r.extra._test_detail || "rejected"})` });
      await load();
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    } finally { setBusy(""); }
  }

  async function remove(id: string) {
    if (!confirm(`Delete stored credential for ${id}?`)) return;
    await api.del(`/api/keys/${id}`);
    await load();
  }

  async function saveDefaults() {
    setMsg(null);
    try {
      await api.put("/api/settings", { config: defaults });
      setMsg({ kind: "ok", text: "Defaults saved — applied to every new analysis." });
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    }
  }

  function ProviderRow({ p }: { p: ProviderInfo }) {
    const k = keyFor(p.id);
    return (
      <tr>
        <td><b>{p.name}</b><div className="muted mono" style={{ fontSize: 11 }}>{p.env}</div></td>
        <td>{k ? <span className="mono">{k.mask || "(endpoint only)"}</span> : <span className="muted">not set</span>}</td>
        <td>{k && <span className={`pill ${k.status}`}>{k.status}</span>}</td>
        <td>
          <div className="row">
            <button className="secondary small" onClick={() => {
              setEditing(p.id); setSecret("");
              const init: Record<string, string> = {};
              for (const f of EXTRA_FIELDS[p.id] ?? []) init[f.key] = f.secret ? "" : (k?.extra[f.key] || "");
              setExtraForm(init);
            }}>
              {k ? "Replace" : "Add"}
            </button>
            {k && <button className="secondary small" disabled={busy === p.id} onClick={() => test(p.id)}>
              {busy === p.id ? "…" : "Test"}
            </button>}
            {k && <button className="danger small" onClick={() => remove(p.id)}>Delete</button>}
          </div>
          {editing === p.id && (
            <div style={{ marginTop: 8 }}>
              {p.kind !== "url" && (
                <input type="password" placeholder={p.id === "bedrock" ? "AWS access key ID" : "API key / secret"}
                  value={secret} onChange={(e) => setSecret(e.target.value)} style={{ marginBottom: 6 }} />
              )}
              {(EXTRA_FIELDS[p.id] ?? []).map((f) => (
                <input key={f.key} type={f.secret ? "password" : "text"}
                  placeholder={f.ph ? `${f.label} (e.g. ${f.ph})` : f.label}
                  value={extraForm[f.key] ?? ""}
                  onChange={(e) => setExtraForm({ ...extraForm, [f.key]: e.target.value })}
                  style={{ marginBottom: 6 }} />
              ))}
              <div className="row">
                <button className="small"
                  disabled={busy === p.id || (!secret && !Object.values(extraForm).some((v) => v.trim()))}
                  onClick={() => save(p)}>Save</button>
                <button className="secondary small" onClick={() => setEditing("")}>Cancel</button>
              </div>
            </div>
          )}
        </td>
      </tr>
    );
  }

  return (
    <div>
      <h1>Settings</h1>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="card">
        <h3>LLM provider keys (bring your own)</h3>
        <p className="muted">Keys are encrypted at rest (Fernet envelope), masked after save, and injected
          into analysis workers just-in-time. They never appear in logs or responses.</p>
        <table>
          <thead><tr><th>Provider</th><th>Stored</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>{providers.map((p) => <ProviderRow key={p.id} p={p} />)}</tbody>
        </table>
      </div>

      <div className="card">
        <h3>Data vendor keys</h3>
        <table>
          <thead><tr><th>Vendor</th><th>Stored</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>{dataProviders.map((p) => <ProviderRow key={p.id} p={p} />)}</tbody>
        </table>
        <p className="muted">yfinance and Polymarket need no key. Alpha Vantage / FRED unlock alternative
          vendor chains for stocks, news, fundamentals, and macro data.</p>
      </div>

      <div className="card">
        <h3>Analysis defaults</h3>
        <div className="grid4">
          <div>
            <label>Default depth</label>
            <select value={defaults.research_depth ?? 1}
              onChange={(e) => setDefaults({ ...defaults, research_depth: +e.target.value })}>
              <option value={1}>Shallow</option><option value={3}>Medium</option><option value={5}>Deep</option>
            </select>
          </div>
          <div>
            <label>Default language</label>
            <select value={defaults.output_language ?? "English"}
              onChange={(e) => setDefaults({ ...defaults, output_language: e.target.value })}>
              {["English", "中文 (Chinese)", "日本語 (Japanese)", "한국어 (Korean)", "Español (Spanish)",
                "Français (French)", "Deutsch (German)", "Português (Portuguese)", "Русский (Russian)", "हिन्दी (Hindi)"]
                .map((l) => <option key={l}>{l}</option>)}
            </select>
          </div>
          <div>
            <label>Trading capital (₹) — for F&O position sizing</label>
            <input value={defaults.trading_capital ?? ""} placeholder="100000"
              onChange={(e) => setDefaults({ ...defaults, trading_capital: e.target.value === "" ? undefined : +e.target.value })} />
          </div>
          <div>
            <label>Risk per trade (%) — of capital, entry→SL</label>
            <input value={defaults.risk_per_trade_pct ?? ""} placeholder="1.0"
              onChange={(e) => setDefaults({ ...defaults, risk_per_trade_pct: e.target.value === "" ? undefined : +e.target.value })} />
          </div>
          <div>
            <label>Benchmark override (blank = auto)</label>
            <input value={defaults.benchmark_ticker ?? ""} placeholder="e.g. QQQ"
              onChange={(e) => setDefaults({ ...defaults, benchmark_ticker: e.target.value || undefined })} />
          </div>
          <div className="checkbox-row" style={{ marginTop: 18 }}>
            <input type="checkbox" id="dckpt" checked={defaults.checkpoint_enabled ?? true}
              onChange={(e) => setDefaults({ ...defaults, checkpoint_enabled: e.target.checked })} />
            <label htmlFor="dckpt" style={{ margin: 0, color: "var(--text)" }}>Checkpointing on by default</label>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <button onClick={saveDefaults}>Save defaults</button>
        </div>
      </div>

      <div className="card">
        <h3>Presets</h3>
        {presets.length === 0 ? <p className="muted">Save presets from the New Analysis page.</p> : (
          <table>
            <thead><tr><th>Name</th><th>Ticker</th><th>Depth</th><th>Mode</th><th></th></tr></thead>
            <tbody>
              {presets.map((p) => (
                <tr key={p.id}>
                  <td><b>{p.name}</b></td>
                  <td>{p.config.ticker}</td>
                  <td>{p.config.research_depth}</td>
                  <td className="muted">{p.config.mode}</td>
                  <td><button className="danger small" onClick={async () => { await api.del(`/api/presets/${p.id}`); load(); }}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
