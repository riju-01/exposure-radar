"""Prompt registry: prompts are versioned files, not strings in code.

Layout:
    prompts/registry.yaml                  feature -> active version (what the app uses)
    prompts/<feature>/<version>.md         YAML front matter (model, params, schema, changelog)
                                           + '# system' and '# user' sections with {{placeholders}}

The prompt's SHA-256 is logged with every call, so a trace always identifies the exact
text that produced it even if a version file is edited in place by mistake.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from salesintel import config

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)
_SECTION = re.compile(r"^# (system|user)\s*$", re.M)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass(frozen=True)
class Prompt:
    feature: str
    version: str
    model: str
    schema: str
    max_tokens: int
    system: str
    user: str
    effort: str | None = None
    fallbacks: bool = False
    changelog: str = ""
    params: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.feature}@{self.version}"

    @property
    def sha(self) -> str:
        return hashlib.sha256(f"{self.model}\n{self.system}\n{self.user}".encode()).hexdigest()[:16]

    def render(self, variables: dict[str, str]) -> tuple[str, str]:
        def sub(text: str) -> str:
            def repl(m: re.Match) -> str:
                if m.group(1) not in variables:
                    raise KeyError(f"{self.id}: missing prompt variable '{m.group(1)}'")
                return str(variables[m.group(1)])
            return _PLACEHOLDER.sub(repl, text)
        return sub(self.system), sub(self.user)


def parse(text: str, *, feature: str, version: str) -> Prompt:
    m = _FRONT_MATTER.match(text)
    if not m:
        raise ValueError(f"{feature}/{version}: missing YAML front matter")
    meta = yaml.safe_load(m.group(1))
    parts = _SECTION.split(m.group(2))
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise ValueError(f"{feature}/{version}: needs exactly '# system' and '# user' sections")
    return Prompt(
        feature=feature, version=version, model=meta["model"], schema=meta["schema"],
        max_tokens=int(meta["max_tokens"]), effort=meta.get("effort"),
        fallbacks=bool(meta.get("fallbacks", False)), changelog=str(meta.get("changelog", "")).strip(),
        system=sections["system"], user=sections["user"],
        params={k: v for k, v in meta.items() if k not in {"model", "schema", "max_tokens", "effort", "fallbacks", "changelog", "feature", "version"}},
    )


@lru_cache(maxsize=None)
def load(feature: str, version: str | None = None) -> Prompt:
    version = version or active_version(feature)
    path = config.PROMPTS_DIR / feature / f"{version}.md"
    return parse(path.read_text(encoding="utf-8"), feature=feature, version=version)


@lru_cache(maxsize=1)
def _registry() -> dict:
    return yaml.safe_load((config.PROMPTS_DIR / "registry.yaml").read_text(encoding="utf-8"))


def active_version(feature: str) -> str:
    return _registry()["features"][feature]["active"]


def versions(feature: str) -> list[str]:
    return sorted(p.stem for p in (config.PROMPTS_DIR / feature).glob("v*.md"))


def features() -> list[str]:
    return list(_registry()["features"])
