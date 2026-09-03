"""AgentAlgo backend configuration.

Deployment-level settings come from environment variables (or backend/.env).
Per-user settings live in the database (see models.Setting).
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("AGENTALGO_DATA_DIR", BACKEND_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    """Minimal .env loader (no external dependency)."""
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _persisted_secret(filename: str, generator) -> str:
    """Load a secret from DATA_DIR, generating and persisting it on first boot.

    Keeps dev setups working with zero configuration while letting production
    deployments override via environment variables.
    """
    path = DATA_DIR / filename
    if path.exists():
        return path.read_text().strip()
    value = generator()
    path.write_text(value)
    path.chmod(0o600)
    return value


# JWT signing secret. Override with AGENTALGO_SECRET_KEY in production.
SECRET_KEY: str = os.getenv("AGENTALGO_SECRET_KEY") or _persisted_secret(
    ".jwt_secret", lambda: secrets.token_urlsafe(48)
)

# Fernet master key for API-key envelope encryption.
# Override with AGENTALGO_FERNET_KEY (must be a valid Fernet key).
def _gen_fernet() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


FERNET_KEY: str = os.getenv("AGENTALGO_FERNET_KEY") or _persisted_secret(
    ".fernet_key", _gen_fernet
)

DATABASE_URL: str = os.getenv(
    "AGENTALGO_DATABASE_URL", f"sqlite:///{DATA_DIR / 'agentalgo.db'}"
)

ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("AGENTALGO_TOKEN_MINUTES", "1440"))

# Per-user concurrent run limit (NFR-S2)
MAX_CONCURRENT_RUNS_PER_USER: int = int(os.getenv("AGENTALGO_MAX_CONCURRENT_RUNS", "3"))

# CORS origins for the frontend dev server / deployed frontend
CORS_ORIGINS: list[str] = os.getenv(
    "AGENTALGO_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

# Super-admin bootstrap: created/promoted at startup. Change these in production.
ADMIN_EMAIL: str = os.getenv("AGENTALGO_ADMIN_EMAIL", "admin@agentalgo.dev")
ADMIN_PASSWORD: str = os.getenv("AGENTALGO_ADMIN_PASSWORD", "admin12345")

# S4: allow user-supplied endpoint URLs (Ollama/vLLM) to point at private/loopback
# addresses. True is right for desktop/self-host (local Ollama); set false on any
# shared/cloud deployment to block SSRF into internal networks.
ALLOW_PRIVATE_URLS: bool = os.getenv("AGENTALGO_ALLOW_PRIVATE_URLS", "true").lower() in (
    "true", "1", "yes",
)

# Demo mode lets users run the full pipeline UX without LLM keys.
DEMO_MODE_AVAILABLE: bool = os.getenv("AGENTALGO_DEMO_MODE", "true").lower() in (
    "true",
    "1",
    "yes",
)
