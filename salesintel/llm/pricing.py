"""Per-model token prices (USD per 1M tokens, Anthropic first-party API, cached 2026-10-06)."""
from __future__ import annotations

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_READ_MULTIPLIER = 0.1  # cache reads bill at ~10% of the input price

OFFLINE_MODELS = {"rules-v0", "template-v0"}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    if model in OFFLINE_MODELS or model not in PRICES_PER_MTOK:
        return 0.0
    p_in, p_out = PRICES_PER_MTOK[model]
    return round((input_tokens * p_in + cache_read_tokens * p_in * CACHE_READ_MULTIPLIER
                  + output_tokens * p_out) / 1_000_000, 6)
