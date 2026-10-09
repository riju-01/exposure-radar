"""Output contracts for every LLM feature.

These JSON Schemas are sent to the API as structured-output formats (so the model cannot
return malformed JSON), and the same schemas validate every response, including offline
fallbacks, before anything reaches the database or the UI.
"""
from __future__ import annotations

import jsonschema

ENTITY_TYPES = [
    "business", "education", "government", "healthcare_provider", "nonprofit",
    "hosting_provider", "telecom_isp", "cdn_saas_platform", "individual_or_hobby", "unknown",
]
# Entity types that are organisations owning and defending their own assets: i.e. buyers.
PROSPECT_ENTITY_TYPES = {"business", "education", "government", "healthcare_provider", "nonprofit"}

INDUSTRIES = [
    "healthcare", "finance", "manufacturing", "education", "retail_ecommerce", "professional_services",
    "technology", "media_entertainment", "government", "nonprofit", "hosting_telecom", "unknown",
]

ACCOUNT_CLASSIFICATION = {
    "type": "object",
    "properties": {
        "entity_type": {"type": "string", "enum": ENTITY_TYPES},
        "is_prospect": {"type": "boolean"},
        "industry": {"type": "string", "enum": INDUSTRIES},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "rationale": {"type": "string"},
    },
    "required": ["entity_type", "is_prospect", "industry", "confidence", "rationale"],
    "additionalProperties": False,
}

OUTREACH_DRAFT = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "why_now": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["point", "evidence"],
                "additionalProperties": False,
            },
        },
        "talk_track": {"type": "array", "items": {"type": "string"}},
        "email": {
            "type": "object",
            "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
            "required": ["subject", "body"],
            "additionalProperties": False,
        },
        "cited_cves": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "why_now", "talk_track", "email", "cited_cves"],
    "additionalProperties": False,
}

SCHEMAS = {"account_classification": ACCOUNT_CLASSIFICATION, "outreach_draft": OUTREACH_DRAFT}


def validate(schema_name: str, payload: dict) -> None:
    """Raise jsonschema.ValidationError if payload breaks the contract."""
    jsonschema.validate(payload, SCHEMAS[schema_name])
