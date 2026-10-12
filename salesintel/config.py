"""Central paths and runtime settings. Everything is overridable by environment variable."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from the project's .env (gitignored, never deployed).

    The project file wins over machine-wide variables: a stale global key (e.g. an old
    GEMINI_API_KEY set with `setx`) must not silently shadow the one configured for this repo.
    Deployments don't ship a .env, so there the platform's environment variables apply."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ[key.strip()] = value


_load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("SI_DATA_DIR", ROOT / "data"))
SLIM_DIR = DATA_DIR / "slim"                      # stage-1 Parquet parts
ACCOUNTS_PARQUET = DATA_DIR / "accounts.parquet"  # stage-3 output
APP_DB = Path(os.getenv("SI_APP_DB", DATA_DIR / "app.db"))

PROMPTS_DIR = ROOT / "prompts"
CONFIG_DIR = ROOT / "config"
EVALS_DIR = ROOT / "evals"
TRACES_DB = Path(os.getenv("SI_TRACES_DB", ROOT / "traces" / "llm_calls.sqlite"))
TRACES_JSONL = Path(os.getenv("SI_TRACES_JSONL", ROOT / "traces" / "llm_calls.jsonl"))
WEB_DIST = ROOT / "web" / "dist"

# LLM runtime controls
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LLM_DAILY_BUDGET_USD = float(os.getenv("SI_LLM_DAILY_BUDGET_USD", "2.00"))
# auto = Anthropic if its key is set, else Gemini if its key is set, else offline rules/templates
LLM_PROVIDER = os.getenv("SI_LLM_PROVIDER", "auto")  # auto | anthropic | gemini | offline

# Gemini tier mapping: prompts declare a Claude tier (haiku = cheap, sonnet/opus = strong);
# when running on Gemini each tier maps to these models. Strong is a fallback chain because the
# free tier frequently answers 503 "high demand" on individual models.
GEMINI_CHEAP_MODEL = os.getenv("SI_GEMINI_CHEAP_MODEL", "gemini-3.5-flash-lite")
GEMINI_STRONG_MODELS = os.getenv("SI_GEMINI_STRONG_MODELS", "gemini-3.8-flash,gemini-3.6-flash,gemini-3-flash-preview").split(",")
GEMINI_MIN_INTERVAL_S = float(os.getenv("SI_GEMINI_MIN_INTERVAL_S", "4"))  # free tier ~15 requests/min
