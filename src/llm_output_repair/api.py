"""Small, application-independent entry points for structured LLM output.

The historical repair_json() default remains tolerant for compatibility.
Use repair() for new code: its JSON default is conservative (SAFE).
"""
from __future__ import annotations

import json
from typing import Any, Literal

import yaml

from .crash_logger import CrashLogger, capture_failure
from .controlled_json import parse_json
from .extract_json import extract_json_new
from .extract_yaml import extract_yaml_new

Format = Literal["json", "yaml"]
Policy = Literal["strict", "safe", "tolerant"]


def _require_text(raw: str) -> None:
    if not isinstance(raw, str):
        raise TypeError("LLM output must be a string")


def repair_json(
    raw: str, *, policy: Policy = "tolerant", crash_logger: CrashLogger | None = None
) -> Any:
    """Decode JSON-like model output, optionally recording terminal failures.

    The default is the original, potentially lossy extractor for backward
    compatibility. SAFE supports verified edits (object roots only).
    No dump is saved unless a CrashLogger is explicitly supplied.
    """
    _require_text(raw)
    if policy not in ("strict", "safe", "tolerant"):
        raise ValueError("Unknown JSON repair policy")
    try:
        if policy == "tolerant":
            return json.loads(extract_json_new(raw))
        expected_type = dict if policy == "safe" else object
        # Capture here, not in the nested parse_json call: one dump per failure.
        return parse_json(raw, policy=policy, expected_type=expected_type).value
    except Exception as error:
        capture_failure(crash_logger, raw, format="json", policy=policy, error=error)
        raise


def repair_yaml(
    raw: str, *, policy: Literal["strict", "tolerant"] = "tolerant",
    crash_logger: CrashLogger | None = None,
) -> Any:
    """Decode YAML-like model output, optionally recording terminal failures.

    'strict' uses PyYAML without repair. 'tolerant' retains the inherited
    normalization engine. No SAFE YAML recovery policy exists.
    """
    _require_text(raw)
    if policy not in ("strict", "tolerant"):
        raise ValueError("YAML supports only 'strict' or 'tolerant' policies")
    try:
        if policy == "strict":
            return yaml.safe_load(raw)
        return extract_yaml_new(raw)
    except Exception as error:
        capture_failure(crash_logger, raw, format="yaml", policy=policy, error=error)
        raise


def repair(
    raw: str, *, format: Format = "json", policy: Policy | None = None,
    crash_logger: CrashLogger | None = None,
) -> Any:
    """Repair structured output; optionally save unrecoverable inputs locally.

    JSON defaults to SAFE, YAML defaults to TOLERANT. Successful recoveries
    are not considered crashes and do not produce dumps.
    """
    if format == "json":
        return repair_json(
            raw, policy="safe" if policy is None else policy, crash_logger=crash_logger
        )
    if format == "yaml":
        return repair_yaml(
            raw, policy="tolerant" if policy is None else policy, crash_logger=crash_logger
        )
    raise ValueError("Unsupported format; expected 'json' or 'yaml'")
