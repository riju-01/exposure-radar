"""LLM providers behind one small interface.

AnthropicProvider  real Claude calls with structured outputs (JSON schema enforced server-side).
OfflineProvider    deterministic rules/templates from llm/offline.py; zero cost, always available.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from salesintel import config
from salesintel.llm.context import AccountContext
from salesintel.llm.offline import OFFLINE
from salesintel.llm.registry import Prompt
from salesintel.llm.schemas import SCHEMAS

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0
    stop_reason: str | None = None
    request_id: str | None = None


class ProviderError(Exception):
    """A call failed in a way the runner should trace and fall back from."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class OfflineProvider:
    name = "offline"

    def complete(self, prompt: Prompt, system: str, user: str, ctx: AccountContext) -> LLMResponse:
        model, fn = OFFLINE[prompt.feature]
        t0 = time.perf_counter()
        text = json.dumps(fn(ctx), ensure_ascii=False)
        return LLMResponse(text=text, model=model, latency_ms=int((time.perf_counter() - t0) * 1000),
                           stop_reason="end_turn")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None):
        import anthropic  # imported lazily so the app runs without the SDK configured

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key or config.ANTHROPIC_API_KEY, max_retries=2, timeout=60)

    def request_params(self, prompt: Prompt, system: str, user: str) -> dict:
        output_config: dict = {"format": {"type": "json_schema", "schema": SCHEMAS[prompt.schema]}}
        if prompt.effort:
            output_config["effort"] = prompt.effort
        params = {
            "model": prompt.model,
            "max_tokens": prompt.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if prompt.fallbacks:
            # On a safety-classifier decline (security content can trip the cyber classifier),
            # the API re-runs the request on Anthropic's recommended fallback model.
            params["betas"] = [FALLBACK_BETA]
            params["extra_body"] = {"fallbacks": "default"}
        return params

    def complete(self, prompt: Prompt, system: str, user: str, ctx: AccountContext) -> LLMResponse:
        a = self._anthropic
        params = self.request_params(prompt, system, user)
        t0 = time.perf_counter()
        try:
            resp = self.client.beta.messages.create(**params)
        except a.RateLimitError as e:
            raise ProviderError(f"rate limited: {e.message}", retryable=True) from e
        except (a.AuthenticationError, a.PermissionDeniedError, a.NotFoundError, a.BadRequestError) as e:
            raise ProviderError(f"{type(e).__name__}: {e.message}", retryable=False) from e
        except a.APIStatusError as e:
            raise ProviderError(f"API {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        except a.APIConnectionError as e:
            raise ProviderError("connection error", retryable=True) from e
        latency = int((time.perf_counter() - t0) * 1000)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        usage = resp.usage
        return LLMResponse(
            text=text, model=resp.model, latency_ms=latency, stop_reason=resp.stop_reason,
            input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            request_id=getattr(resp, "_request_id", None),
        )


def live_available() -> bool:
    return bool(config.ANTHROPIC_API_KEY) and config.LLM_PROVIDER != "offline"


def default_provider():
    return AnthropicProvider() if live_available() else OfflineProvider()
