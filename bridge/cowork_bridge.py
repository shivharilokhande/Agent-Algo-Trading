"""AgentAlgo Cowork Bridge — run the TradingAgents engine on your Claude subscription.

A tiny OpenAI-compatible server (default http://127.0.0.1:8765/v1) that translates
each /v1/chat/completions call into one `claude -p` invocation (Claude Code CLI),
which bills your Claude subscription — no Anthropic/OpenAI API key required.

Supports what the engine needs from the OpenAI protocol:
  • plain chat completions
  • tool calling (schemas injected into the prompt; JSON tool_calls parsed back)
  • forced tool_choice / structured output (json_schema response_format)

Personal-use bridge: it shells out to YOUR logged-in Claude Code CLI on YOUR
machine. Subscription rate limits apply. Not for multi-tenant deployments.

Run:  .venv/bin/python bridge/cowork_bridge.py   (or via run_dev.sh)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cowork-bridge")

BRIDGE_DIR = Path(__file__).resolve().parent
SETTINGS_OVERRIDE = BRIDGE_DIR / "claude_settings.json"
CLAUDE_BIN = os.getenv("COWORK_BRIDGE_CLAUDE_BIN", "claude")
MAX_PARALLEL = int(os.getenv("COWORK_BRIDGE_PARALLEL", "2"))
CALL_TIMEOUT = int(os.getenv("COWORK_BRIDGE_TIMEOUT", "600"))

_semaphore = asyncio.Semaphore(MAX_PARALLEL)
app = FastAPI(title="AgentAlgo Cowork Bridge")

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _map_model(requested: str) -> str:
    r = (requested or "").lower()
    if "haiku" in r or "quick" in r or "fast" in r:
        return "haiku"
    if "opus" in r:
        return "opus"
    return "sonnet"


def _render_messages(messages: list[dict]) -> str:
    """Flatten an OpenAI message list into a single transcript prompt."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content") or ""
        if isinstance(content, list):  # multimodal chunks → text only
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role == "system":
            parts.append(f"<system_instructions>\n{content}\n</system_instructions>")
        elif role == "tool":
            parts.append(
                f"<tool_result tool_call_id=\"{m.get('tool_call_id', '')}\">\n{content}\n</tool_result>"
            )
        elif role == "assistant":
            if m.get("tool_calls"):
                calls = [
                    {"name": tc["function"]["name"],
                     "arguments": json.loads(tc["function"].get("arguments") or "{}")}
                    for tc in m["tool_calls"]
                ]
                parts.append(f"<assistant_tool_calls>\n{json.dumps(calls)}\n</assistant_tool_calls>")
            if content:
                parts.append(f"<assistant>\n{content}\n</assistant>")
        else:
            parts.append(f"<user>\n{content}\n</user>")
    return "\n\n".join(parts)


def _tool_instructions(tools: list[dict], tool_choice) -> str:
    schemas = []
    for t in tools:
        fn = t.get("function", t)
        schemas.append({
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        })
    forced = ""
    if isinstance(tool_choice, dict):
        forced_name = tool_choice.get("function", {}).get("name")
        if forced_name:
            forced = (
                f"\nYou MUST call the tool `{forced_name}` now — respond with the "
                f"tool-call JSON only, nothing else."
            )
    return (
        "\n\n<available_tools>\n" + json.dumps(schemas, indent=1) + "\n</available_tools>\n"
        "Tool protocol: to call one or more tools, respond with ONLY a JSON object, "
        'no prose, in exactly this form:\n'
        '{"tool_calls": [{"name": "<tool name>", "arguments": { ... }}]}\n'
        "When you have everything you need, respond with your final answer as plain "
        "text (never JSON, never mention the protocol)." + forced
    )


def _json_schema_instructions(response_format: dict) -> str:
    schema = (response_format.get("json_schema") or {}).get("schema") or {}
    return (
        "\n\nRespond with ONLY a valid JSON object matching this schema — no prose, "
        "no code fences:\n" + json.dumps(schema, indent=1)
    )


def _extract_json(text: str) -> dict | None:
    """Best-effort: fenced JSON, whole-string JSON, or first balanced object."""
    m = _JSON_BLOCK.search(text)
    candidates = [m.group(1)] if m else []
    candidates.append(text.strip())
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return None


async def _call_claude(prompt: str, model: str) -> str:
    """One `claude -p` invocation on the user's subscription."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "ANTHROPIC", "CLAUDECODE"))}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(Path.home()))
    # neutralize any global redirection (Ollama/OpenRouter overrides in ~/.claude)
    env.update({"ANTHROPIC_BASE_URL": "", "ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""})

    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN, "-p", "--output-format", "json", "--max-turns", "1",
        "--model", model, "--settings", str(SETTINGS_OVERRIDE),
        "--tools", "",  # pure text generation — no local tool use inside the CLI
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, cwd=str(BRIDGE_DIR),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()), timeout=CALL_TIMEOUT
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise HTTPException(status_code=504, detail="claude CLI call timed out") from exc
    if proc.returncode != 0:
        raise HTTPException(status_code=502,
                            detail=f"claude CLI exited {proc.returncode}: {stderr.decode()[-300:]}")
    try:
        payload = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="claude CLI returned non-JSON") from exc
    if payload.get("is_error"):
        raise HTTPException(status_code=502, detail=f"claude: {payload.get('result', 'error')[:300]}")
    return payload.get("result") or ""


@app.get("/v1/models")
def models():
    now = int(time.time())
    return {"object": "list", "data": [
        {"id": mid, "object": "model", "created": now, "owned_by": "cowork-bridge"}
        for mid in ("cowork", "cowork-haiku", "cowork-sonnet", "cowork-opus")
    ]}


@app.get("/health")
def health():
    return {"status": "ok", "bridge": "cowork", "parallel": MAX_PARALLEL}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    if body.get("stream"):
        # engine uses non-streaming; reject clearly rather than half-support SSE
        raise HTTPException(status_code=400, detail="stream=true not supported by the bridge")
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    tool_choice = body.get("tool_choice")
    response_format = body.get("response_format") or {}
    model = _map_model(body.get("model", ""))

    prompt = _render_messages(messages)
    expects_tools = bool(tools)
    if expects_tools:
        prompt += _tool_instructions(tools, tool_choice)
    if response_format.get("type") in ("json_schema", "json_object"):
        prompt += _json_schema_instructions(response_format)

    async with _semaphore:
        started = time.time()
        text = await _call_claude(prompt, model)
        log.info("claude(%s) %.1fs, %d chars, tools=%s", model, time.time() - started,
                 len(text), expects_tools)

    message: dict = {"role": "assistant", "content": text}
    finish = "stop"
    if expects_tools:
        parsed = _extract_json(text)
        if parsed and isinstance(parsed.get("tool_calls"), list) and parsed["tool_calls"]:
            calls = []
            for tc in parsed["tool_calls"][:8]:
                if not isinstance(tc, dict) or not tc.get("name"):
                    continue
                calls.append({
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": str(tc["name"]),
                                 "arguments": json.dumps(tc.get("arguments") or {})},
                })
            if calls:
                message = {"role": "assistant", "content": None, "tool_calls": calls}
                finish = "tool_calls"
        elif parsed and isinstance(tool_choice, dict):
            # forced structured output returned as a bare object → wrap as the forced call
            forced_name = tool_choice.get("function", {}).get("name")
            if forced_name:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                    "function": {"name": forced_name, "arguments": json.dumps(parsed)},
                }]}
                finish = "tool_calls"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"cowork-{model}",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": max(1, len(prompt) // 4),
                  "completion_tokens": max(1, len(text) // 4),
                  "total_tokens": max(2, (len(prompt) + len(text)) // 4)},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("COWORK_BRIDGE_PORT", "8765")))
