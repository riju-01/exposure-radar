"""LLM providers behind one small interface.

AnthropicProvider  real Claude calls with structured outputs (JSON schema enforced server-side).
GeminiProvider     Google Gemini (free tier used to measure evals without a paid key); same contract.
OfflineProvider    deterministic rules/templates from llm/offline.py; zero cost, always available.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import httpx

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


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Gemini finish reasons that mean "declined", mapped onto the runner's refusal handling.
_GEMINI_BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}


def parse_gemini_response(data: dict, requested_model: str) -> LLMResponse:
    """Map a generateContent response onto LLMResponse (pure function, unit-tested)."""
    usage = data.get("usageMetadata") or {}
    out_tokens = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    model = data.get("modelVersion") or requested_model
    if (data.get("promptFeedback") or {}).get("blockReason"):
        return LLMResponse(text="", model=model, input_tokens=usage.get("promptTokenCount") or 0,
                           stop_reason="refusal", request_id=data.get("responseId"))
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    finish = cand.get("finishReason", "STOP")
    stop = "refusal" if finish in _GEMINI_BLOCKED else "max_tokens" if finish == "MAX_TOKENS" else "end_turn"
    return LLMResponse(text=text, model=model, input_tokens=usage.get("promptTokenCount") or 0,
                       output_tokens=out_tokens, cache_read_tokens=usage.get("cachedContentTokenCount") or 0,
                       stop_reason=stop, request_id=data.get("responseId"))


class GeminiProvider:
    """Gemini via REST. Prompts keep their Claude tier; this maps cheap/strong onto Gemini models.

    Free-tier realities handled here: a minimum interval between calls (rate limit), retries with
    backoff on 429/5xx, and a model fallback chain for the strong tier when a model is overloaded.
    """

    name = "gemini"
    _lock = threading.Lock()
    _last_call = 0.0

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None, max_attempts: int = 4):
        self.api_key = api_key or config.GEMINI_API_KEY
        self.client = client or httpx.Client(timeout=120)
        self.max_attempts = max_attempts

    @staticmethod
    def models_for(prompt: Prompt) -> list[str]:
        return [config.GEMINI_CHEAP_MODEL] if "haiku" in prompt.model else list(config.GEMINI_STRONG_MODELS)

    @staticmethod
    def request_body(prompt: Prompt, system: str, user: str) -> dict:
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseJsonSchema": SCHEMAS[prompt.schema],
                                 "maxOutputTokens": prompt.max_tokens},
        }

    def _throttle(self) -> float:
        """Space calls to respect the free-tier rate limit; returns seconds waited."""
        with GeminiProvider._lock:
            wait = GeminiProvider._last_call + config.GEMINI_MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            GeminiProvider._last_call = time.monotonic()
            return max(wait, 0.0)

    def complete(self, prompt: Prompt, system: str, user: str, ctx: AccountContext) -> LLMResponse:
        body = self.request_body(prompt, system, user)
        models = self.models_for(prompt)
        t0 = time.perf_counter()
        paced = 0.0  # self-imposed rate-limit waits are excluded from latency; retries/backoff are not
        last_error = "no attempt made"
        for attempt in range(self.max_attempts):
            model = models[attempt % len(models)]
            paced += self._throttle()
            try:
                r = self.client.post(GEMINI_URL.format(model=model), json=body,
                                     headers={"x-goog-api-key": self.api_key})
            except httpx.HTTPError as e:
                last_error = f"connection error: {e}"
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                resp = parse_gemini_response(r.json(), model)
                resp.latency_ms = int((time.perf_counter() - t0 - paced) * 1000)
                return resp
            try:
                message = (r.json().get("error") or {}).get("message", "")
            except ValueError:
                message = r.text[:200]
            last_error = f"{model} HTTP {r.status_code}: {message[:200]}"
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(30, 2 ** (attempt + 1)))  # overloaded or rate limited: back off, rotate model
                continue
            raise ProviderError(last_error, retryable=False)
        raise ProviderError(f"gave up after {self.max_attempts} attempts: {last_error}", retryable=True)


def active_provider_name() -> str:
    """Which provider the app uses: 'anthropic', 'gemini' or 'offline'."""
    keys = {"anthropic": config.ANTHROPIC_API_KEY, "gemini": config.GEMINI_API_KEY, "offline": "always"}
    if config.LLM_PROVIDER in keys:
        return config.LLM_PROVIDER if keys[config.LLM_PROVIDER] else "offline"
    if config.ANTHROPIC_API_KEY:
        return "anthropic"
    if config.GEMINI_API_KEY:
        return "gemini"
    return "offline"


def live_available() -> bool:
    return active_provider_name() != "offline"


def default_provider():
    return {"anthropic": AnthropicProvider, "gemini": GeminiProvider,
            "offline": OfflineProvider}[active_provider_name()]()
