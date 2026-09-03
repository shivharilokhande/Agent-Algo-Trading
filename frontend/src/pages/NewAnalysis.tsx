import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ProviderInfo, RunOut } from "../api";

interface Options {
  analysts: { id: string; label: string; desc: string }[];
  depths: { id: number; label: string; hint: string }[];
  languages: string[];
  thinking_knobs: Record<string, { key: string; label: string; options: string[] }>;
}

export default function NewAnalysis() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [opts, setOpts] = useState<Options | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [demoAvailable, setDemoAvailable] = useState(true);
  const [presets, setPresets] = useState<{ id: string; name: string; config: any }[]>([]);

  // form state
  const [ticker, setTicker] = useState("NVDA");
  const [preview, setPreview] = useState<any>(null);
  const [previewErr, setPreviewErr] = useState("");
  const [tradeDate, setTradeDate] = useState(new Date().toISOString().slice(0, 10));
  const [analysts, setAnalysts] = useState<string[]>(["market", "social", "news", "fundamentals"]);
  const [depth, setDepth] = useState(1);
  const [mode, setMode] = useState<"demo" | "engine">("demo");
  const [provider, setProvider] = useState("");
  const [models, setModels] = useState<{ quick: any[]; deep: any[] }>({ quick: [], deep: [] });
  const [quickModel, setQuickModel] = useState("");
  const [deepModel, setDeepModel] = useState("");
  const [customQuick, setCustomQuick] = useState("");
  const [customDeep, setCustomDeep] = useState("");
  const [language, setLanguage] = useState("English");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [temperature, setTemperature] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [retries, setRetries] = useState("");
  const [thinking, setThinking] = useState("");
  const [checkpoint, setCheckpoint] = useState(true);
  const [presetName, setPresetName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<Options>("/api/catalog/options").then(setOpts);
    api.get<any>("/api/catalog/providers").then((d) => {
      setProviders(d.llm_providers);
      setDemoAvailable(d.demo_mode);
      const configured = d.llm_providers.filter((p: ProviderInfo) => p.configured);
      if (configured.length > 0) setProvider(configured[0].id);
    });
    api.get<any[]>("/api/presets").then(setPresets).catch(() => {});
    const again = params.get("again");
    if (again) {
      api.get<RunOut>(`/api/runs/${again}`).then((r) => applyConfig(r.config));
    }
    // apply saved defaults
    api.get<{ config: any }>("/api/settings").then((s) => {
      if (s.config && Object.keys(s.config).length) applyConfig(s.config, true);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyConfig(c: any, defaultsOnly = false) {
    if (!defaultsOnly && c.ticker) setTicker(String(c.ticker));
    if (c.analysts) setAnalysts(c.analysts);
    if (c.research_depth) setDepth(c.research_depth);
    if (c.mode) setMode(c.mode);
    if (c.llm_provider && c.llm_provider !== "demo") setProvider(c.llm_provider);
    if (c.output_language) setLanguage(c.output_language);
    if (c.quick_think_llm) setQuickModel(c.quick_think_llm);
    if (c.deep_think_llm) setDeepModel(c.deep_think_llm);
    if (c.checkpoint_enabled !== undefined) setCheckpoint(!!c.checkpoint_enabled);
  }

  // ticker preview with debounce (F3.1)
  useEffect(() => {
    if (!ticker.trim()) return;
    const t = setTimeout(() => {
      api.get<any>(`/api/catalog/ticker/${encodeURIComponent(ticker.trim())}`)
        .then((p) => { setPreview(p); setPreviewErr(""); })
        .catch((e) => { setPreview(null); setPreviewErr(e.message); });
    }, 500);
    return () => clearTimeout(t);
  }, [ticker]);

  // model options per provider (F3.6)
  useEffect(() => {
    if (!provider || mode !== "engine") return;
    api.get<any>(`/api/catalog/models/${provider}`).then((m) => {
      setModels(m);
      setQuickModel(m.quick[0]?.id ?? "custom");
      setDeepModel(m.deep[0]?.id ?? "custom");
    });
  }, [provider, mode]);

  const isCrypto = preview?.asset_type === "crypto";
  const effectiveAnalysts = useMemo(
    () => (isCrypto ? analysts.filter((a) => a !== "fundamentals") : analysts),
    [analysts, isCrypto]
  );
  const knob = opts?.thinking_knobs[provider];
  const estCalls = useMemo(() => {
    const base = effectiveAnalysts.length * 2 + 2;
    return base + depth * 2 + depth * 3 + 2;
  }, [effectiveAnalysts, depth]);

  function toggleAnalyst(id: string) {
    setAnalysts((a) => (a.includes(id) ? a.filter((x) => x !== id) : [...a, id]));
  }

  function buildConfig() {
    return {
      ticker: ticker.trim(),
      trade_date: tradeDate,
      analysts: effectiveAnalysts,
      research_depth: depth,
      mode,
      llm_provider: mode === "engine" ? provider : "demo",
      quick_think_llm: quickModel === "custom" ? customQuick : quickModel,
      deep_think_llm: deepModel === "custom" ? customDeep : deepModel,
      output_language: language,
      temperature: temperature === "" ? null : parseFloat(temperature),
      max_tokens: maxTokens === "" ? null : parseInt(maxTokens),
      llm_max_retries: retries === "" ? null : parseInt(retries),
      checkpoint_enabled: checkpoint,
      ...(knob && thinking ? { [knob.key]: thinking } : {}),
    };
  }

  async function start() {
    setErr(""); setBusy(true);
    try {
      const run = await api.post<RunOut>("/api/runs", buildConfig());
      nav(`/runs/${run.id}`);
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  async function savePreset() {
    if (!presetName.trim()) return;
    await api.post("/api/presets", { name: presetName.trim(), config: buildConfig() });
    setPresets(await api.get("/api/presets"));
    setPresetName("");
  }

  if (!opts) return <p className="muted">Loading…</p>;
  const configuredProviders = providers.filter((p) => p.configured);

  return (
    <div>
      <h1>New Analysis</h1>

      {presets.length > 0 && (
        <div className="row" style={{ marginBottom: 12 }}>
          <span className="muted">Presets:</span>
          {presets.map((p) => (
            <button key={p.id} className="secondary small" onClick={() => applyConfig(p.config)}>{p.name}</button>
          ))}
        </div>
      )}

      <div className="card">
        <div className="grid2">
          <div>
            <label>Ticker — any Yahoo Finance market (AAPL, 0700.HK, RELIANCE.NS, BTC-USD…)</label>
            <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} />
            {preview && (
              <p className="muted" style={{ marginBottom: 0 }}>
                ✓ <b>{preview.name}</b> · {preview.symbol} · {preview.asset_type}
                {preview.exchange && <> · {preview.exchange}</>}
                {preview.currency && <> · {preview.currency}</>} · benchmark {preview.benchmark}
              </p>
            )}
            {previewErr && <p style={{ color: "var(--red)", marginBottom: 0 }}>{previewErr}</p>}
          </div>
          <div>
            <label>Analysis date</label>
            <input type="date" value={tradeDate} max={new Date().toISOString().slice(0, 10)}
              onChange={(e) => setTradeDate(e.target.value)} />
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Analyst team</h3>
        <div className="grid2">
          {opts.analysts.map((a) => {
            const disabled = isCrypto && a.id === "fundamentals";
            return (
              <div key={a.id} className="checkbox-row" style={{ opacity: disabled ? 0.45 : 1 }}>
                <input type="checkbox" id={a.id} disabled={disabled}
                  checked={effectiveAnalysts.includes(a.id)} onChange={() => toggleAnalyst(a.id)} />
                <label htmlFor={a.id} style={{ margin: 0, color: "var(--text)" }}>
                  <b>{a.label}</b> <span className="muted">— {a.desc}{disabled ? " (n/a for crypto)" : ""}</span>
                </label>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card">
        <h3>Research depth</h3>
        <div className="grid3">
          {opts.depths.map((d) => (
            <div key={d.id} className="agent-cell" style={{ cursor: "pointer", borderColor: depth === d.id ? "var(--accent)" : undefined }}
              onClick={() => setDepth(d.id)}>
              <div><b>{d.label}</b><div className="muted" style={{ fontSize: 12 }}>{d.hint}</div></div>
              <input type="radio" checked={depth === d.id} readOnly />
            </div>
          ))}
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>≈ {estCalls} LLM calls at this depth.</p>
      </div>

      <div className="card">
        <h3>Engine</h3>
        <div className="row" style={{ marginBottom: 12 }}>
          {demoAvailable && (
            <div className="checkbox-row">
              <input type="radio" id="m-demo" checked={mode === "demo"} onChange={() => setMode("demo")} />
              <label htmlFor="m-demo" style={{ margin: 0, color: "var(--text)" }}>
                <b>Demo mode</b> <span className="muted">— simulated agents, no keys needed</span>
              </label>
            </div>
          )}
          <div className="checkbox-row">
            <input type="radio" id="m-engine" checked={mode === "engine"} onChange={() => setMode("engine")} />
            <label htmlFor="m-engine" style={{ margin: 0, color: "var(--text)" }}>
              <b>Live engine</b> <span className="muted">— real TradingAgents run on your keys</span>
            </label>
          </div>
        </div>

        {mode === "engine" && (
          <>
            {configuredProviders.length === 0 ? (
              <div className="error-box">
                No provider keys configured. Add one in Settings → API Keys first.
              </div>
            ) : (
              <div className="grid3">
                <div>
                  <label>LLM provider</label>
                  <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                    {configuredProviders.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}{p.status === "valid" ? " ✓" : ""}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label>Quick-think model</label>
                  <select value={quickModel} onChange={(e) => setQuickModel(e.target.value)}>
                    {models.quick.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                  {quickModel === "custom" && (
                    <input placeholder="model id" value={customQuick} onChange={(e) => setCustomQuick(e.target.value)} style={{ marginTop: 6 }} />
                  )}
                </div>
                <div>
                  <label>Deep-think model</label>
                  <select value={deepModel} onChange={(e) => setDeepModel(e.target.value)}>
                    {models.deep.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                  {deepModel === "custom" && (
                    <input placeholder="model id" value={customDeep} onChange={(e) => setCustomDeep(e.target.value)} style={{ marginTop: 6 }} />
                  )}
                </div>
              </div>
            )}
          </>
        )}

        <div style={{ marginTop: 12 }} className="grid3">
          <div>
            <label>Report language (debate stays English)</label>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {opts.languages.map((l) => <option key={l}>{l}</option>)}
            </select>
          </div>
          <div className="checkbox-row" style={{ marginTop: 18 }}>
            <input type="checkbox" id="ckpt" checked={checkpoint} onChange={(e) => setCheckpoint(e.target.checked)} />
            <label htmlFor="ckpt" style={{ margin: 0, color: "var(--text)" }}>Checkpoint / resume enabled</label>
          </div>
        </div>

        <p style={{ marginBottom: 0 }}>
          <a href="#" onClick={(e) => { e.preventDefault(); setShowAdvanced(!showAdvanced); }}>
            {showAdvanced ? "▾ Hide" : "▸ Show"} advanced settings
          </a>
        </p>
        {showAdvanced && (
          <div className="grid4" style={{ marginTop: 10 }}>
            <div><label>Temperature (blank = provider default)</label>
              <input value={temperature} onChange={(e) => setTemperature(e.target.value)} placeholder="e.g. 0.0" /></div>
            <div><label>Max output tokens</label>
              <input value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} placeholder="e.g. 8192" /></div>
            <div><label>LLM retry budget</label>
              <input value={retries} onChange={(e) => setRetries(e.target.value)} placeholder="e.g. 5" /></div>
            {mode === "engine" && knob && (
              <div><label>{knob.label}</label>
                <select value={thinking} onChange={(e) => setThinking(e.target.value)}>
                  {knob.options.map((o) => <option key={o} value={o}>{o || "provider default"}</option>)}
                </select></div>
            )}
          </div>
        )}
      </div>

      {err && <div className="error-box">{err}</div>}
      <div className="row">
        <button disabled={busy || !preview || effectiveAnalysts.length === 0} onClick={start}>
          {busy ? "Starting…" : "▶ Start analysis"}
        </button>
        <input placeholder="Save as preset…" value={presetName} onChange={(e) => setPresetName(e.target.value)} style={{ width: 180 }} />
        <button className="secondary" onClick={savePreset} disabled={!presetName.trim()}>Save preset</button>
      </div>
    </div>
  );
}
