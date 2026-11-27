"""Deterministic extraction and heuristic repair of LLM-generated JSON/YAML."""
from __future__ import annotations

import json as _json
from typing import Any

from .extract_json import extract_json_new as extract_json_new
from .extract_yaml import extract_yaml_new as extract_yaml_new, dump_yaml as dump_yaml
from .yaml_normalizer import YamlNormalizer as YamlNormalizer
from .crash_recorder import CrashRecorder as CrashRecorder, _capture_failure
from .controlled_json import JsonResult as JsonResult, parse_json as _parse_json

__version__ = "0.1.0"


def repair_json(raw: str, *, recorder: CrashRecorder | None = None) -> Any:
    """Return decoded JSON using the inherited tolerant extractor."""
    try:
        return _json.loads(extract_json_new(raw))
    except Exception as exc:
        _capture_failure(recorder, operation="repair_json", raw=raw, error=exc)
        raise


def repair_yaml(raw: str, *, recorder: CrashRecorder | None = None) -> Any:
    """Return parsed YAML using the inherited tolerant extractor."""
    try:
        return extract_yaml_new(raw)
    except Exception as exc:
        _capture_failure(recorder, operation="repair_yaml", raw=raw, error=exc)
        raise


def parse_json(
    raw: str,
    *,
    policy: str = "safe",
    expected_type: type = dict,
    on_rejected=None,
    recorder: CrashRecorder | None = None,
) -> JsonResult:
    """Controlled JSON parsing, optionally recording *final* parse failures.

    The existing on_rejected callback is unchanged: it may observe an interim
    rejection that is successfully repaired. The recorder only observes final
    failures after every permitted repair strategy has been attempted.
    """
    try:
        return _parse_json(
            raw, policy=policy, expected_type=expected_type, on_rejected=on_rejected
        )
    except Exception as exc:
        _capture_failure(
            recorder,
            operation="parse_json",
            raw=raw,
            error=exc,
            options={
                "policy": policy,
                "expected_type": getattr(expected_type, "__name__", type(expected_type).__name__),
            },
        )
        raise
