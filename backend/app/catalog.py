"""Provider/model/config catalog (F3.5–F3.8).

Ported from tradingagents.llm_clients.model_catalog (v0.4.0) so the wizard
works even when the engine package is not importable. When the engine IS
installed, get_model_options() defers to it so the catalog never drifts.
"""
from __future__ import annotations

CUSTOM = [{"label": "Custom model ID", "id": "custom"}]

_GLM = {
    "quick": [
        {"label": "GLM-5.3-Air - Fast, thinking-capable", "id": "glm-5.3-air"},
        {"label": "GLM-4.7-Flash - Cost-efficient", "id": "glm-4.7-flash"},
    ]
    + CUSTOM,
    "deep": [
        {"label": "GLM-5.3 - Flagship, thinking mode", "id": "glm-5.3"},
        {"label": "GLM-5.0 - Previous flagship", "id": "glm-5.0"},
    ]
    + CUSTOM,
}
_QWEN = {
    "quick": [
        {"label": "Qwen3.5-Turbo - Fast and economical", "id": "qwen3.5-turbo"},
        {"label": "Qwen3-Max - Balanced", "id": "qwen3-max"},
    ]
    + CUSTOM,
    "deep": [
        {"label": "Qwen3.5-Max - Flagship reasoning", "id": "qwen3.5-max"},
        {"label": "Qwen3-Max - Previous flagship", "id": "qwen3-max"},
    ]
    + CUSTOM,
}
_MINIMAX = {
    "quick": [{"label": "MiniMax-M2.5 - Fast agentic", "id": "minimax-m2.5"}] + CUSTOM,
    "deep": [{"label": "MiniMax-M2.5 - Flagship", "id": "minimax-m2.5"}] + CUSTOM,
}

MODEL_OPTIONS: dict[str, dict[str, list[dict]]] = {
    "openai": {
        "quick": [
            {"label": "GPT-5.6 Luna - Fast, cost-efficient frontier", "id": "gpt-5.6-luna"},
            {"label": "GPT-5.6 Terra - Balances intelligence and cost", "id": "gpt-5.6-terra"},
            {"label": "GPT-5.4 Mini - Fast, strong coding and tool use", "id": "gpt-5.4-mini"},
        ],
        "deep": [
            {"label": "GPT-5.6 - Latest frontier reasoning (Sol)", "id": "gpt-5.6"},
            {"label": "GPT-5.6 Terra - Balances intelligence and cost", "id": "gpt-5.6-terra"},
            {"label": "GPT-5.5 - Previous-gen frontier, 1M context", "id": "gpt-5.5"},
            {"label": "GPT-5.4 - Cost-effective, 1M context", "id": "gpt-5.4"},
        ],
    },
    "anthropic": {
        "quick": [
            {"label": "Claude Sonnet 5 - Best speed/intelligence balance", "id": "claude-sonnet-5"},
            {"label": "Claude Haiku 4.5 - Fastest", "id": "claude-haiku-4-5"},
        ],
        "deep": [
            {"label": "Claude Fable 5 - Most capable", "id": "claude-fable-5"},
            {"label": "Claude Opus 4.8 - Frontier agentic reasoning", "id": "claude-opus-4-8"},
            {"label": "Claude Sonnet 5 - Near-frontier at Sonnet cost", "id": "claude-sonnet-5"},
            {"label": "Claude Opus 4.7 - Previous frontier", "id": "claude-opus-4-7"},
        ],
    },
    "google": {
        "quick": [
            {"label": "Gemini 3.5 Flash - Latest, frontier agentic (GA)", "id": "gemini-3.5-flash"},
            {"label": "Gemini 3.1 Flash Lite - Most cost-efficient", "id": "gemini-3.1-flash-lite"},
        ],
        "deep": [
            {"label": "Gemini 3.1 Pro - Reasoning-first (preview)", "id": "gemini-3.1-pro-preview"},
            {"label": "Gemini 3.5 Flash - Latest GA", "id": "gemini-3.5-flash"},
        ],
    },
    "xai": {
        "quick": [
            {"label": "Grok 4.3 - Latest flagship", "id": "grok-4.3"},
            {"label": "Grok 4.20 (Non-Reasoning) - Speed-optimized", "id": "grok-4.20-0309-non-reasoning"},
            {"label": "Grok Build 0.1 - Coding-specialized", "id": "grok-build-0.1"},
        ],
        "deep": [
            {"label": "Grok 4.3 - Latest flagship, 1M ctx", "id": "grok-4.3"},
            {"label": "Grok 4.20 (Reasoning)", "id": "grok-4.20-0309-reasoning"},
            {"label": "Grok 4.20 Multi-Agent", "id": "grok-4.20-multi-agent-0309"},
        ],
    },
    "deepseek": {
        "quick": [{"label": "DeepSeek V4 Flash - Fast, thinking-capable", "id": "deepseek-v4-flash"}] + CUSTOM,
        "deep": [
            {"label": "DeepSeek V4 Pro - Latest flagship", "id": "deepseek-v4-pro"},
            {"label": "DeepSeek V4 Flash - Fast", "id": "deepseek-v4-flash"},
        ]
        + CUSTOM,
    },
    "qwen": _QWEN,
    "qwen-cn": _QWEN,
    "glm": _GLM,
    "glm-cn": _GLM,
    "minimax": _MINIMAX,
    "minimax-cn": _MINIMAX,
    "openrouter": {"quick": CUSTOM, "deep": CUSTOM},  # fetched live when key present
    "ollama": {
        "quick": [
            {"label": "Qwen3:latest (8B)", "id": "qwen3:latest"},
            {"label": "GPT-OSS:latest (20B)", "id": "gpt-oss:latest"},
            {"label": "GLM-4.7-Flash:latest (30B)", "id": "glm-4.7-flash:latest"},
        ]
        + CUSTOM,
        "deep": [
            {"label": "GLM-4.7-Flash:latest (30B)", "id": "glm-4.7-flash:latest"},
            {"label": "GPT-OSS:latest (20B)", "id": "gpt-oss:latest"},
            {"label": "Qwen3:latest (8B)", "id": "qwen3:latest"},
        ]
        + CUSTOM,
    },
    "azure": {"quick": CUSTOM, "deep": CUSTOM},
    "bedrock": {"quick": CUSTOM, "deep": CUSTOM},
    "openai_compatible": {"quick": CUSTOM, "deep": CUSTOM},
    "mistral": {"quick": CUSTOM, "deep": CUSTOM},
    "kimi": {"quick": CUSTOM, "deep": CUSTOM},
    "groq": {"quick": CUSTOM, "deep": CUSTOM},
    "nvidia": {"quick": CUSTOM, "deep": CUSTOM},
}

# credential requirements per provider: kind = key | url | key+url | aws
PROVIDERS: list[dict] = [
    {"id": "openai", "name": "OpenAI (GPT)", "kind": "key", "env": "OPENAI_API_KEY"},
    {"id": "anthropic", "name": "Anthropic (Claude)", "kind": "key", "env": "ANTHROPIC_API_KEY"},
    {"id": "google", "name": "Google (Gemini)", "kind": "key", "env": "GOOGLE_API_KEY"},
    {"id": "xai", "name": "xAI (Grok)", "kind": "key", "env": "XAI_API_KEY"},
    {"id": "deepseek", "name": "DeepSeek", "kind": "key", "env": "DEEPSEEK_API_KEY"},
    {"id": "qwen", "name": "Qwen — International", "kind": "key", "env": "DASHSCOPE_API_KEY"},
    {"id": "qwen-cn", "name": "Qwen — China", "kind": "key", "env": "DASHSCOPE_CN_API_KEY"},
    {"id": "glm", "name": "GLM via Z.AI (intl)", "kind": "key", "env": "ZHIPU_API_KEY"},
    {"id": "glm-cn", "name": "GLM via BigModel (CN)", "kind": "key", "env": "ZHIPU_CN_API_KEY"},
    {"id": "minimax", "name": "MiniMax — Global", "kind": "key", "env": "MINIMAX_API_KEY"},
    {"id": "minimax-cn", "name": "MiniMax — China", "kind": "key", "env": "MINIMAX_CN_API_KEY"},
    {"id": "openrouter", "name": "OpenRouter", "kind": "key", "env": "OPENROUTER_API_KEY"},
    {"id": "mistral", "name": "Mistral", "kind": "key", "env": "MISTRAL_API_KEY"},
    {"id": "kimi", "name": "Kimi (Moonshot)", "kind": "key", "env": "MOONSHOT_API_KEY"},
    {"id": "groq", "name": "Groq", "kind": "key", "env": "GROQ_API_KEY"},
    {"id": "nvidia", "name": "NVIDIA NIM", "kind": "key", "env": "NVIDIA_API_KEY"},
    {"id": "ollama", "name": "Ollama (local/remote)", "kind": "url", "env": "OLLAMA_BASE_URL"},
    {"id": "openai_compatible", "name": "OpenAI-compatible / Cowork bridge (free, uses your Claude subscription)", "kind": "key+url", "env": "OPENAI_COMPATIBLE_API_KEY"},
    {"id": "azure", "name": "Azure OpenAI", "kind": "key+url", "env": "AZURE_OPENAI_API_KEY"},
    {"id": "bedrock", "name": "AWS Bedrock", "kind": "aws", "env": "AWS_ACCESS_KEY_ID"},
]

DATA_PROVIDERS: list[dict] = [
    {"id": "alpha_vantage", "name": "Alpha Vantage (stocks/news/fundamentals)", "kind": "key", "env": "ALPHA_VANTAGE_API_KEY"},
    {"id": "fred", "name": "FRED (macro indicators)", "kind": "key", "env": "FRED_API_KEY"},
    {"id": "kite", "name": "Zerodha Kite Connect (live NSE spot + option quotes — data only, never orders)",
     "kind": "key", "env": "KITE_API_KEY"},
]

DEPTHS = [
    {"id": 1, "label": "Shallow", "hint": "Quick research, 1 debate round — fewest LLM calls"},
    {"id": 3, "label": "Medium", "hint": "Moderate debate and risk discussion (3 rounds)"},
    {"id": 5, "label": "Deep", "hint": "Comprehensive, 5 debate rounds — most tokens"},
]

ANALYSTS = [
    {"id": "market", "label": "Market Analyst", "desc": "Technical indicators (MACD, RSI …) on verified price data"},
    {"id": "social", "label": "Sentiment Analyst", "desc": "News headlines + StockTwits + Reddit mood"},
    {"id": "news", "label": "News Analyst", "desc": "Global news and macroeconomic events"},
    {"id": "fundamentals", "label": "Fundamentals Analyst", "desc": "Financial statements and intrinsic value"},
]

LANGUAGES = [
    "English", "中文 (Chinese)", "日本語 (Japanese)", "한국어 (Korean)",
    "Español (Spanish)", "Français (French)", "Deutsch (German)",
    "Português (Portuguese)", "Русский (Russian)", "हिन्दी (Hindi)",
]

THINKING_KNOBS = {
    "google": {"key": "google_thinking_level", "label": "Gemini thinking level", "options": ["", "minimal", "low", "medium", "high"]},
    "openai": {"key": "openai_reasoning_effort", "label": "Reasoning effort", "options": ["", "low", "medium", "high"]},
    "anthropic": {"key": "anthropic_effort", "label": "Effort", "options": ["", "low", "medium", "high"]},
}

RATINGS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
RATING_REVIEW = "REVIEW"


def get_model_options(provider: str) -> dict[str, list[dict]]:
    """Prefer the installed engine's catalog; fall back to the port above."""
    try:  # pragma: no cover — engine optional
        from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS as ENGINE_OPTS

        opts = ENGINE_OPTS.get(provider.lower())
        if opts:
            return {
                mode: [{"label": label, "id": mid} for label, mid in pairs]
                for mode, pairs in opts.items()
            }
    except ImportError:
        pass
    return MODEL_OPTIONS.get(provider.lower(), {"quick": CUSTOM, "deep": CUSTOM})
