"""The single entry point for every LLM feature call.

    run_feature("account-classify", ctx)  ->  FeatureResult

Pipeline per call:
    load versioned prompt -> render -> budget guard -> provider call -> refusal check
    -> JSON parse -> schema validation -> trace -> (on any failure) offline fallback, also traced

Callers never talk to a provider directly, so every call is traced, costed, budgeted and
validated the same way, in the app, in evals and in batch jobs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import jsonschema

from salesintel import config
from salesintel.llm import registry
from salesintel.llm.context import AccountContext
from salesintel.llm.pricing import cost_usd
from salesintel.llm.providers import AnthropicProvider, OfflineProvider, ProviderError, default_provider
from salesintel.llm.schemas import validate
from salesintel.llm.tracing import TraceRecord, Tracer, default_tracer


@dataclass
class FeatureResult:
    output: dict
    status: str              # ok | fallback
    provider: str
    model: str
    prompt_version: str
    call_id: str
    cost_usd: float
    latency_ms: int
    note: str | None = None  # why we fell back, if we did


def _decision(feature: str, out: dict) -> str:
    if feature == "account-classify":
        return f"{out['entity_type']}/{'prospect' if out['is_prospect'] else 'not_prospect'}/{out['industry']}"
    if feature == "outreach-draft":
        return f"draft:{len(out['why_now'])}pts:{len(out['email']['body'].split())}w"
    return "ok"


def _attempt(provider, prompt, system, user, ctx, *, tracer, env, run_id, account_id) -> tuple[dict | None, TraceRecord]:
    request = {"system": system, "user": user, "model": prompt.model, "max_tokens": prompt.max_tokens,
               "effort": prompt.effort, "schema": prompt.schema}
    rec = TraceRecord(env=env, run_id=run_id, feature=prompt.feature, prompt_version=prompt.version,
                      prompt_sha=prompt.sha, provider=provider.name, model=prompt.model,
                      account_id=account_id, request_json=json.dumps(request, ensure_ascii=False), status="error")
    try:
        resp = provider.complete(prompt, system, user, ctx)
    except ProviderError as e:
        rec.error = str(e)
        return None, rec

    rec.model, rec.response_text, rec.stop_reason = resp.model, resp.text, resp.stop_reason
    rec.input_tokens, rec.output_tokens, rec.cache_read_tokens = resp.input_tokens, resp.output_tokens, resp.cache_read_tokens
    rec.latency_ms, rec.request_id = resp.latency_ms, resp.request_id
    rec.cost_usd = cost_usd(resp.model, resp.input_tokens, resp.output_tokens, resp.cache_read_tokens)

    if resp.stop_reason == "refusal":
        rec.status = "refused"
        return None, rec
    if resp.stop_reason == "max_tokens":
        rec.status, rec.error = "invalid_output", "truncated at max_tokens"
        return None, rec
    try:
        out = json.loads(resp.text)
        validate(prompt.schema, out)
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        rec.status, rec.error = "invalid_output", str(e)[:500]
        return None, rec

    rec.status = "ok"
    rec.output_json = json.dumps(out, ensure_ascii=False)
    rec.decision = _decision(prompt.feature, out)
    return out, rec


def run_feature(feature: str, ctx: AccountContext, variables: dict[str, str] | None = None, *,
                version: str | None = None, provider=None, env: str = "app", run_id: str | None = None,
                tracer: Tracer | None = None, allow_fallback: bool = True) -> FeatureResult:
    tracer = tracer or default_tracer()
    prompt = registry.load(feature, version)
    variables = variables or {"account_card": ctx.account_card(), "evidence_pack": ctx.evidence_pack()}
    system, user = prompt.render(variables)
    provider = provider or default_provider()
    account_id = ctx.account.get("id")
    note = None

    if isinstance(provider, AnthropicProvider) and env == "app" \
            and tracer.spend_today("app") >= config.LLM_DAILY_BUDGET_USD:
        tracer.log(TraceRecord(env=env, run_id=run_id, feature=feature, prompt_version=prompt.version,
                               prompt_sha=prompt.sha, provider=provider.name, model=prompt.model,
                               account_id=account_id, request_json="{}", status="budget_blocked",
                               error=f"daily budget ${config.LLM_DAILY_BUDGET_USD:.2f} reached"))
        note = "daily LLM budget reached"
    else:
        out, rec = _attempt(provider, prompt, system, user, ctx, tracer=tracer, env=env,
                            run_id=run_id, account_id=account_id)
        tracer.log(rec)
        if out is not None:
            return FeatureResult(out, "ok", provider.name, rec.model, prompt.version, rec.call_id,
                                 rec.cost_usd, rec.latency_ms or 0)
        note = f"{rec.status}: {rec.error or rec.stop_reason}"
        if not allow_fallback:
            raise RuntimeError(f"{feature} failed: {note}")

    offline = OfflineProvider()
    out, rec = _attempt(offline, prompt, system, user, ctx, tracer=tracer, env=env,
                        run_id=run_id, account_id=account_id)
    if out is None:
        raise RuntimeError(f"offline fallback for {feature} produced invalid output: {rec.error}")
    if not isinstance(provider, OfflineProvider):
        rec.status = "fallback"
        rec.error = note
    tracer.log(rec)
    return FeatureResult(out, "fallback" if note else "ok", offline.name, rec.model, prompt.version,
                         rec.call_id, 0.0, rec.latency_ms or 0, note)
