"""Engine-mode runner: executes the real TradingAgents graph in a subprocess.

Isolation design (NFR-S1/S2): the user's decrypted keys are passed only in the
child's environment; the child streams JSON-line events on stdout which the
parent converts to run events. Cancellation kills the process group; the
engine's own LangGraph checkpointing (checkpoint_enabled) makes the run
resumable afterwards.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from ..config import DATA_DIR
from ..db import SessionLocal
from ..models import ApiKey, Run
from ..security import decrypt_secret
from . import RunCancelled, RunHandle, RunManager

WORKER = Path(__file__).with_name("engine_worker.py")

# provider id -> env var (engine parity: llm_clients/api_key_env.py)
PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "qwen-cn": "DASHSCOPE_CN_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "glm-cn": "ZHIPU_CN_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_CN_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "groq": "GROQ_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "alpha_vantage": "ALPHA_VANTAGE_API_KEY",
    "fred": "FRED_API_KEY",
}


# S2: providers whose credentials a run may need besides its LLM provider
_DATA_PROVIDERS = {"alpha_vantage", "fred"}


def _user_env(user_id: str, llm_provider: str) -> dict[str, str]:
    """Decrypt ONLY the credentials this run needs into env-var form (S2)."""
    env: dict[str, str] = {}
    wanted = {llm_provider} | _DATA_PROVIDERS
    with SessionLocal() as db:
        keys = (
            db.query(ApiKey)
            .filter(ApiKey.user_id == user_id, ApiKey.provider.in_(wanted))
            .all()
        )
        for k in keys:
            secret = decrypt_secret(k.ciphertext) if k.ciphertext else ""
            var = PROVIDER_ENV.get(k.provider)
            if var and secret:
                env[var] = secret
            extra = json.loads(k.extra_json or "{}")

            def _safe_url(raw: str) -> str | None:
                """M1: re-validate stored endpoints at run launch (flag may have changed)."""
                from ..routers.keys import validate_outbound_url

                try:
                    return validate_outbound_url(raw)
                except Exception:
                    return None

            if k.provider == "ollama" and extra.get("base_url"):
                if url := _safe_url(extra["base_url"]):
                    env["OLLAMA_BASE_URL"] = url
            if k.provider == "openai_compatible" and extra.get("base_url"):
                if url := _safe_url(extra["base_url"]):
                    env["TRADINGAGENTS_LLM_BACKEND_URL"] = url
            if k.provider == "azure":
                if extra.get("endpoint") and (url := _safe_url(extra["endpoint"])):
                    env["AZURE_OPENAI_ENDPOINT"] = url
                if extra.get("api_version"):
                    env["AZURE_OPENAI_API_VERSION"] = extra["api_version"]
            if k.provider == "bedrock":
                if secret:
                    env["AWS_ACCESS_KEY_ID"] = secret
                if extra.get("secret_access_key"):  # H1: stored encrypted since v1.1
                    try:
                        env["AWS_SECRET_ACCESS_KEY"] = decrypt_secret(extra["secret_access_key"])
                    except Exception:  # legacy plaintext value
                        env["AWS_SECRET_ACCESS_KEY"] = extra["secret_access_key"]
                if extra.get("region"):
                    env["AWS_DEFAULT_REGION"] = extra["region"]
    return env


async def run_engine(
    mgr: RunManager, handle: RunHandle, run_id: str, config: dict, resume: bool = False
) -> dict:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        user_id, ticker, trade_date = run.user_id, run.ticker, run.trade_date

    user_dir = DATA_DIR / "users" / user_id
    user_dir.mkdir(parents=True, exist_ok=True)

    # S2: minimal allow-listed environment — never the parent's full env,
    # never AGENTALGO_* platform secrets, only this run's provider keys.
    _ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM", "SSL_CERT_FILE",
              "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
    env = {k: v for k, v in os.environ.items() if k in _ALLOW}
    env.update(_user_env(user_id, config.get("llm_provider", "openai")))
    # per-user engine home: decision log + checkpoints + results (F8/F9 parity)
    env["TRADINGAGENTS_MEMORY_LOG_PATH"] = str(user_dir / "trading_memory.md")
    env["TRADINGAGENTS_CACHE_DIR"] = str(user_dir / "cache")
    env["TRADINGAGENTS_RESULTS_DIR"] = str(user_dir / "logs")
    env["PYTHONUNBUFFERED"] = "1"

    payload = json.dumps({"config": config, "ticker": ticker, "trade_date": trade_date, "resume": resume})

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(WORKER),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,  # own process group so cancel kills the tree
    )
    proc.stdin.write(payload.encode() + b"\n")
    await proc.stdin.drain()
    proc.stdin.close()

    result: dict = {"rating": None, "decision_summary": "", "stats": {}}
    stderr_task = asyncio.create_task(proc.stderr.read())

    try:
        while True:
            if handle.cancel_event.is_set():
                raise RunCancelled()
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            try:
                msg = json.loads(line.decode())
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "report_section":
                await mgr.save_report(handle, msg["section"], msg.get("content_md", ""))
            elif mtype == "result":
                result = {
                    "rating": msg.get("rating"),
                    "decision_summary": msg.get("decision_summary", ""),
                    "stats": msg.get("stats", {}),
                }
            elif mtype in ("agent_status", "message", "tool_call", "stats"):
                await mgr.emit(handle, mtype, agent=msg.get("agent", ""), payload=msg.get("payload", {}))
        rc = await proc.wait()
        if rc != 0:
            stderr = (await stderr_task).decode(errors="replace")
            # surface the root-cause exception line, not a traceback fragment
            lines = [l for l in stderr.strip().splitlines() if l.strip()]
            cause = ""
            for line in reversed(lines):
                if not line.startswith(" ") and not line.startswith("^"):
                    cause = line.strip()
                    break
            # S8: scrub anything key-shaped before persisting the error
            import re

            # L1: broad key-shape redaction (OpenAI/Anthropic/Google/AWS/GitHub/Slack …)
            cause = re.sub(
                r"\b(sk-|key-|Bearer\s+|AIza|AKIA|ASIA|ghp_|gho_|xox[bap]-)[A-Za-z0-9_\-\./+]+",
                "[redacted]", cause,
            )
            raise RuntimeError(f"Engine worker failed: {cause[:500] or f'exit code {rc}'}")
        return result
    except RunCancelled:
        _kill(proc)
        raise
    except Exception:
        _kill(proc)
        raise
    finally:
        if not stderr_task.done():
            stderr_task.cancel()


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
