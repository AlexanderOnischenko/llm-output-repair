"""Small, application-independent entry points for structured LLM output.

The historical repair_json() default remains tolerant for compatibility.
Use repair() for new code: its JSON default is conservative (SAFE).
"""
from __future__ import annotations

import json
from typing import Any, Literal

import yaml

from .controlled_json import parse_json
from .extract_json import extract_json_new
from .extract_yaml import extract_yaml_new

Format = Literal["json", "yaml"]
Policy = Literal["strict", "safe", "tolerant"]


def _require_text(raw: str) -> None:
    if not isinstance(raw, str):
        raise TypeError("LLM output must be a string")


def repair_json(raw: str, *, policy: Policy = "tolerant") -> Any:
    """Return a Python value from JSON-like model output.

    The default is the original, potentially lossy extractor for backward
    compatibility. Use policy="safe" to limit repairs to verified edits
    (object documents only), or policy="strict" to disable repair.
    SAFE enforces a size limit; the legacy tolerant path does not.
    """
    _require_text(raw)
    if policy not in ("strict", "safe", "tolerant"):
        raise ValueError("Unknown JSON repair policy")
    if policy == "tolerant":
        return json.loads(extract_json_new(raw))
    expected_type = dict if policy == "safe" else object
    return parse_json(raw, policy=policy, expected_type=expected_type).value


def repair_yaml(raw: str, *, policy: Literal["strict", "tolerant"] = "tolerant") -> Any:
    """Return a Python value from YAML-like model output.

    'strict' parses using PyYAML without attempting repairs. 'tolerant'
    retains the existing heuristic YAML normalization. There is no SAFE
    YAML recovery policy: repaired YAML may change its meaning.
    """
    _require_text(raw)
    if policy == "strict":
        return yaml.safe_load(raw)
    if policy == "tolerant":
        return extract_yaml_new(raw)
    raise ValueError("YAML supports only 'strict' or 'tolerant' policies")


def repair(raw: str, *, format: Format = "json", policy: Policy | None = None) -> Any:
    """Repair and decode structured output without selecting an extractor.

    JSON defaults to SAFE (object-only); YAML defaults to TOLERANT.
    Select policy explicitly when different behavior is required.
    """
    if format == "json":
        return repair_json(raw, policy="safe" if policy is None else policy)
    if format == "yaml":
        return repair_yaml(raw, policy="tolerant" if policy is None else policy)
    raise ValueError("Unsupported format; expected 'json' or 'yaml'")
