"""Application-facing entry points for structured LLM output."""
from __future__ import annotations

import json
from typing import Any, Literal

import yaml

from .crash_logger import CrashLogger, capture_failure
from .crash_recorder import CrashRecorder, _capture_failure
from .controlled_json import parse_json

Format = Literal["json", "yaml"]
Policy = Literal["strict", "safe", "tolerant"]


def _require_text(raw: str) -> None:
    if not isinstance(raw, str):
        raise TypeError("LLM output must be a string")


def repair_json(
    raw: str,
    *,
    policy: Policy = "tolerant",
    crash_logger: CrashLogger | None = None,
    recorder: CrashRecorder | None = None,
) -> Any:
    """Decode JSON-like output with the historical tolerant default."""
    from . import extract_json_new

    if policy not in ("strict", "safe", "tolerant"):
        raise ValueError("Unknown JSON repair policy")
    if policy != "tolerant":
        _require_text(raw)
        return parse_json(
            raw, policy=policy, expected_type=dict if policy == "safe" else object,
            crash_logger=crash_logger, recorder=recorder,
        ).value
    try:
        return json.loads(extract_json_new(raw))
    except Exception as error:
        _capture_failure(recorder, operation="repair_json", raw=raw, error=error)
        capture_failure(crash_logger, raw, format="json", policy=policy, error=error)
        raise


def repair_yaml(
    raw: str,
    *,
    policy: Literal["strict", "tolerant"] = "tolerant",
    crash_logger: CrashLogger | None = None,
    recorder: CrashRecorder | None = None,
) -> Any:
    """Decode YAML-like output with the historical tolerant default."""
    from . import extract_yaml_new

    if policy not in ("strict", "tolerant"):
        raise ValueError("YAML supports only 'strict' or 'tolerant' policies")
    try:
        if policy == "strict":
            _require_text(raw)
            return yaml.safe_load(raw)
        return extract_yaml_new(raw)
    except Exception as error:
        _capture_failure(
            recorder, operation="repair_yaml", raw=raw, error=error,
            options={"policy": "strict"} if policy == "strict" else None,
        )
        capture_failure(crash_logger, raw, format="yaml", policy=policy, error=error)
        raise


def repair(
    raw: str,
    *,
    format: Format = "json",
    policy: Policy | None = None,
    crash_logger: CrashLogger | None = None,
    recorder: CrashRecorder | None = None,
) -> Any:
    """Repair structured output, using SAFE for JSON and TOLERANT for YAML."""
    _require_text(raw)
    if format == "json":
        return repair_json(
            raw, policy="safe" if policy is None else policy,
            crash_logger=crash_logger, recorder=recorder,
        )
    if format == "yaml":
        return repair_yaml(
            raw, policy="tolerant" if policy is None else policy,
            crash_logger=crash_logger, recorder=recorder,
        )
    raise ValueError("Unsupported format; expected 'json' or 'yaml'")
