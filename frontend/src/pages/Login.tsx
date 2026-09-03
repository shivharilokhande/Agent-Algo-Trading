import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../api";

export default function Login() {
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = await api.post<{ access_token: string }>(`/api/auth/${mode}`, { email, password });
      setToken(r.access_token);
      nav("/");
    } catch (ex: any) {
      setErr(ex.message || "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        <div className="logo-text" style={{ marginBottom: 6 }}>AgentAlgo</div>
        <p className="muted">Multi-agent LLM trading research platform</p>
        <form onSubmit={submit}>
          <div style={{ marginBottom: 12 }}>
            <label>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div style={{ marginBottom: 16 }}>
            <label>Password {mode === "register" && <span className="muted">(min 8 chars)</span>}</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={mode === "register" ? 8 : 1} />
          </div>
          {err && <div className="error-box">{err}</div>}
          <button disabled={busy} style={{ width: "100%" }}>
            {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <p className="muted" style={{ marginTop: 14, textAlign: "center" }}>
          {mode === "login" ? (
            <>No account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("register"); setErr(""); }}>Register</a></>
          ) : (
            <>Have an account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("login"); setErr(""); }}>Sign in</a></>
          )}
        </p>
        <p className="disclaimer" style={{ textAlign: "center" }}>
          Research purposes only — not financial advice.
        </p>
      </div>
    </div>
  );
}
