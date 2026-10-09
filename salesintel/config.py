"""Central paths and runtime settings. Everything is overridable by environment variable."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
LLM_DAILY_BUDGET_USD = float(os.getenv("SI_LLM_DAILY_BUDGET_USD", "2.00"))
LLM_PROVIDER = os.getenv("SI_LLM_PROVIDER", "auto")  # auto | anthropic | offline
