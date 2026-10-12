"""LLM layer: prompt registry, guardrails, and the runner's failure handling (no network)."""
import copy
import json

import pytest

from salesintel.llm import registry
from salesintel.llm.grounding import check_outreach
from salesintel.llm.offline import classify_rules, outreach_template
from salesintel.llm.providers import AnthropicProvider, LLMResponse, ProviderError
from salesintel.llm.runner import run_feature
from salesintel.llm.schemas import PROSPECT_ENTITY_TYPES, validate

GOOD_DRAFT = {
    "summary": "Acme Dental runs a small self-hosted estate with a known-exploited Apache flaw.",
    "why_now": [{"point": "Apache 2.4.49 with CVE-2021-41773 (CISA KEV)", "evidence": ["E1"]},
                {"point": "Remote desktop reachable from the internet", "evidence": ["E2"]}],
    "talk_track": ["Ask who owns patching", "Offer the findings list", "Suggest a 15-minute call"],
    "email": {"subject": "Two internet-facing findings for Acme Dental",
              "body": "Hi {first_name},\n\nPublic scan data shows a web server affected by CVE-2021-41773 and a "
                      "remote-desktop service reachable from the internet. You may already know. Want the full list?\n\n{sender_name}"},
    "cited_cves": ["CVE-2021-41773"],
}


# ---------------------------------------------------------------- registry
@pytest.mark.parametrize("feature", ["account-classify", "outreach-draft"])
def test_every_prompt_version_parses_and_renders(feature, ctx):
    for v in registry.versions(feature):
        p = registry.load(feature, v)
        system, user = p.render({"account_card": ctx.account_card(), "evidence_pack": ctx.evidence_pack()})
        assert "{{" not in system + user
        assert p.model.startswith("claude-") and p.schema and p.changelog
        assert len(p.sha) == 16


def test_active_and_candidate_exist():
    for f in registry.features():
        spec = registry._registry()["features"][f]
        assert spec["active"] in registry.versions(f)
        assert spec["candidate"] in registry.versions(f)


def test_missing_variable_is_an_error():
    with pytest.raises(KeyError):
        registry.load("outreach-draft", "v2").render({"account_card": "x"})


# ---------------------------------------------------------------- guardrails
def test_grounded_draft_passes(ctx):
    res = check_outreach(GOOD_DRAFT, ctx)
    assert res["passed"], res["issues"]
    assert res["metrics"]["cves_grounded"] == 1


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d["email"].update(body=d["email"]["body"] + " Also CVE-2024-99999."), "not in evidence"),
    (lambda d: d["why_now"].append({"point": "made up", "evidence": ["E9"]}), "unknown evidence ids"),
    (lambda d: d["why_now"].append({"point": "no refs", "evidence": []}), "without evidence"),
    (lambda d: d["email"].update(body="Your server 1.2.3.4 is exposed."), "raw IP"),
    (lambda d: d["email"].update(body="We believe you have been breached."), "forbidden phrasing"),
    (lambda d: d["email"].update(body="word " * 200), "too long"),
    (lambda d: d["email"].update(body="We noticed port 8080 is open."), "ports not in evidence"),
])
def test_guardrails_catch_bad_drafts(ctx, mutate, expected):
    draft = copy.deepcopy(GOOD_DRAFT)
    mutate(draft)
    res = check_outreach(draft, ctx)
    assert not res["passed"]
    assert any(expected in issue for issue in res["issues"]), res["issues"]


# ---------------------------------------------------------------- offline fallbacks
def test_offline_outputs_satisfy_schemas_and_guardrails(ctx):
    cls = classify_rules(ctx)
    validate("account_classification", cls)
    assert cls["is_prospect"] == (cls["entity_type"] in PROSPECT_ENTITY_TYPES)
    draft = outreach_template(ctx)
    validate("outreach_draft", draft)
    assert check_outreach(draft, ctx)["passed"]


# ---------------------------------------------------------------- runner
class FakeProvider(AnthropicProvider):
    """Looks like the live provider to the runner, but returns canned responses."""

    def __init__(self, response=None, error=None):
        self.response, self.error = response, error

    def complete(self, prompt, system, user, ctx):
        if self.error:
            raise self.error
        return self.response


def _resp(payload, stop="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return LLMResponse(text=text, model="claude-haiku-5-5", input_tokens=1000, output_tokens=100,
                       latency_ms=420, stop_reason=stop, request_id="req_1")


VALID_CLS = {"entity_type": "healthcare_provider", "is_prospect": True, "industry": "healthcare",
             "confidence": "high", "rationale": "dental practice"}


def test_runner_ok_path_traces_cost_and_decision(ctx, tracer):
    res = run_feature("account-classify", ctx, provider=FakeProvider(_resp(VALID_CLS)), tracer=tracer, env="eval")
    assert res.status == "ok" and res.output == VALID_CLS
    row = tracer.query("select * from llm_calls")[0]
    assert row["status"] == "ok" and row["decision"] == "healthcare_provider/prospect/healthcare"
    assert row["cost_usd"] == pytest.approx((1000 * 0.10 + 100 * 0.50) / 1e6)
    assert row["prompt_sha"] and row["request_id"] == "req_1"
    assert tracer.jsonl_path.read_text().count("\n") == 1


@pytest.mark.parametrize("provider,status", [
    (FakeProvider(_resp("not json")), "invalid_output"),
    (FakeProvider(_resp({"entity_type": "alien"})), "invalid_output"),
    (FakeProvider(_resp(VALID_CLS, stop="refusal")), "refused"),
    (FakeProvider(_resp(VALID_CLS, stop="max_tokens")), "invalid_output"),
    (FakeProvider(error=ProviderError("rate limited", retryable=True)), "error"),
])
def test_runner_falls_back_and_traces_both_attempts(ctx, tracer, provider, status):
    res = run_feature("account-classify", ctx, provider=provider, tracer=tracer, env="eval")
    assert res.status == "fallback" and res.provider == "offline"
    validate("account_classification", res.output)
    rows = tracer.query("select status, provider from llm_calls order by ts")
    assert [r["status"] for r in rows] == [status, "fallback"]


def test_budget_guard_blocks_live_calls(ctx, tracer, monkeypatch):
    from salesintel import config
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET_USD", 0.0)
    res = run_feature("account-classify", ctx, provider=FakeProvider(_resp(VALID_CLS)), tracer=tracer, env="app")
    assert res.status == "fallback" and "budget" in res.note
    assert tracer.query("select status from llm_calls")[0]["status"] == "budget_blocked"


def test_live_request_uses_structured_output_and_fallbacks():
    p = registry.load("outreach-draft", "v2")
    params = AnthropicProvider.request_params(object.__new__(AnthropicProvider), p, "S", "U")
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["output_config"]["effort"] == "medium"
    assert params["extra_body"] == {"fallbacks": "default"}


# ---------------------------------------------------------------- gemini provider (no network)
def test_gemini_response_parsing():
    from salesintel.llm.providers import parse_gemini_response

    ok = parse_gemini_response({
        "candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"a": 1}'}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 60, "thoughtsTokenCount": 140},
        "modelVersion": "gemini-3.5-flash-lite", "responseId": "r1"}, "gemini-3.5-flash-lite")
    assert ok.text == '{"a": 1}' and ok.stop_reason == "end_turn"
    assert (ok.input_tokens, ok.output_tokens) == (900, 200)  # thinking tokens are billed as output
    assert parse_gemini_response({"candidates": [{"finishReason": "SAFETY"}]}, "m").stop_reason == "refusal"
    assert parse_gemini_response({"promptFeedback": {"blockReason": "OTHER"}}, "m").stop_reason == "refusal"
    assert parse_gemini_response({"candidates": [{"content": {"parts": [{"text": "{"}]},
                                                  "finishReason": "MAX_TOKENS"}]}, "m").stop_reason == "max_tokens"


def test_gemini_tier_mapping_and_request_shape():
    from salesintel import config
    from salesintel.llm.providers import GeminiProvider

    cheap, strong = registry.load("account-classify", "v2"), registry.load("outreach-draft", "v2")
    assert GeminiProvider.models_for(cheap) == [config.GEMINI_CHEAP_MODEL]
    assert GeminiProvider.models_for(strong) == config.GEMINI_STRONG_MODELS
    body = GeminiProvider.request_body(cheap, "S", "U")
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["required"]


def test_gemini_retries_overload_then_succeeds(ctx, tracer, monkeypatch):
    import httpx
    from salesintel import config
    from salesintel.llm.providers import GeminiProvider

    monkeypatch.setattr(config, "GEMINI_MIN_INTERVAL_S", 0)
    monkeypatch.setattr("salesintel.llm.providers.time.sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(VALID_CLS)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}, "modelVersion": "gemini-x"})

    provider = GeminiProvider(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = run_feature("account-classify", ctx, provider=provider, tracer=tracer, env="eval")
    assert res.status == "ok" and res.output == VALID_CLS and len(calls) == 2
