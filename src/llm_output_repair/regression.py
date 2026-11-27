"""Data-only replay and regression fixtures for captured LLM parser failures.

A crash is an observation, not a specification. Promotion requires an explicit
expected value or exception type. No LLM calls, uploads, dynamic imports or
arbitrary Python execution occur when replaying a fixture.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .crash_recorder import SCHEMA_VERSION, _ALLOWED_OPERATIONS

MAX_CASE_BYTES = 16_000_000
_ALLOWED_POLICIES = frozenset(("strict", "safe", "tolerant"))
_ROOT_TYPES: dict[str, type] = {
    "dict": dict, "list": list, "str": str, "int": int,
    "float": float, "bool": bool, "NoneType": type(None),
}
_ERROR_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}\Z")
_BUCKETS = ("known_bad", "fixed")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key in case JSON")
        result[key] = value
    return result


def _no_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON constant is not allowed")


def _decode_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _validate_input(case: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if case.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported case schema_version")
    operation = case.get("operation")
    if operation not in _ALLOWED_OPERATIONS:
        raise ValueError("Unsupported operation in case")
    data = case.get("input")
    if not isinstance(data, dict) or set(data) != {"raw", "options"}:
        raise ValueError("Case input must contain raw and options")
    if not isinstance(data["raw"], str) or not isinstance(data["options"], dict):
        raise ValueError("Case raw/options must be string/object")
    options = data["options"]
    if operation == "parse_json":
        if set(options) != {"policy", "expected_type"}:
            raise ValueError("parse_json needs policy and expected_type")
        if options["policy"] not in _ALLOWED_POLICIES:
            raise ValueError("Unknown policy in case")
        if options["expected_type"] not in _ROOT_TYPES:
            raise ValueError("Unknown expected_type in case")
    elif options:
        raise ValueError("repair_json/repair_yaml do not accept options")
    return operation, data


def case_digest(case: Mapping[str, Any]) -> str:
    """Same identity scheme as CrashRecorder v1, independent of timestamps."""
    operation, data = _validate_input(case)
    identity = {
        "schema_version": SCHEMA_VERSION, "operation": operation, "input": data
    }
    return hashlib.sha256(_json_bytes(identity)).hexdigest()


def _validate_expected(expected: Any) -> None:
    if not isinstance(expected, dict):
        raise ValueError("Missing explicit expected result")
    status = expected.get("status")
    if status == "accepted":
        if set(expected) != {"status", "value"}:
            raise ValueError("Accepted fixture requires an exact JSON value")
        _json_bytes(expected["value"])  # reject unrepresentable / nonfinite values
    elif status == "rejected":
        if set(expected) != {"status", "exception_type"}:
            raise ValueError("Rejected fixture requires exception_type")
        name = expected["exception_type"]
        if not isinstance(name, str) or not _ERROR_NAME.fullmatch(name):
            raise ValueError("Invalid exception type")
    else:
        raise ValueError("Expected status must be accepted or rejected")


def load_case(path: str | Path) -> dict[str, Any]:
    """Read a bounded JSON dump or fixture. Never execute input as code."""
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ValueError("Case must be a regular non-symlink JSON file")
    if p.stat().st_size > MAX_CASE_BYTES:
        raise ValueError("Case file exceeds replay size limit")
    obj = _decode_json(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Case must be a JSON object")
    _validate_input(obj)
    if "expected" in obj:
        _validate_expected(obj["expected"])
        if obj.get("kind") != "regression_fixture":
            raise ValueError("Expected result is only valid in a regression fixture")
    elif obj.get("kind") == "regression_fixture":
        raise ValueError("Regression fixture is missing an expected result")
    elif not isinstance(obj.get("observed"), dict) or obj["observed"].get("status") != "failed":
        raise ValueError("Not a recorder dump or regression fixture")
    if "case_id" in obj and obj["case_id"] != case_digest(obj):
        raise ValueError("Case identifier does not match input hash")
    return obj


@dataclass(frozen=True)
class Observation:
    status: str
    value: Any = None
    exception_type: str | None = None

    def safe_summary(self) -> dict[str, str]:
        """Never include raw model output or exception messages in CLI output."""
        summary = {"status": self.status}
        if self.exception_type is not None:
            summary["exception_type"] = self.exception_type
        return summary


def replay_case(case: Mapping[str, Any]) -> Observation:
    """Run exactly the saved public operation, without invoking a model."""
    operation, inputs = _validate_input(case)
    raw = inputs["raw"]
    options = inputs["options"]
    from . import parse_json, repair_json, repair_yaml

    try:
        if operation == "parse_json":
            value = parse_json(
                raw,
                policy=options["policy"],
                expected_type=_ROOT_TYPES[options["expected_type"]],
            ).value
        elif operation == "repair_json":
            value = repair_json(raw)
        else:
            value = repair_yaml(raw)
    except Exception as exc:
        return Observation(status="rejected", exception_type=type(exc).__name__)
    return Observation(status="accepted", value=value)


def matches_expected(case: Mapping[str, Any], observation: Observation) -> bool:
    expected = case.get("expected")
    _validate_expected(expected)
    if observation.status != expected["status"]:
        return False
    if observation.status == "rejected":
        return observation.exception_type == expected["exception_type"]
    try:
        # Canonical JSON comparison distinguishes e.g. True from 1 and ignores
        # irrelevant dict insertion order. It rejects non-JSON output values.
        return _json_bytes(observation.value) == _json_bytes(expected["value"])
    except (TypeError, ValueError, OverflowError):
        return False


def fixture_paths(corpus: str | Path, bucket: str = "fixed") -> Iterator[Path]:
    """Scan only fixed/known_bad JSON fixtures, never raw dump folders."""
    if bucket not in (*_BUCKETS, "all"):
        raise ValueError("bucket must be fixed, known_bad or all")
    root = Path(corpus)
    for name in (_BUCKETS if bucket == "all" else (bucket,)):
        directory = root / name
        if directory.is_symlink():
            raise ValueError("Fixture bucket may not be a symlink")
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError("Fixture bucket is not a directory")
        yield from sorted(directory.glob("*.json"))


def promote_dump(
    dump_path: str | Path,
    *,
    corpus: str | Path,
    expected: Mapping[str, Any],
    bucket: str = "known_bad",
) -> Path:
    """Turn an observed crash into a *reviewed*, deterministic test fixture.

    Known-bad cases must currently fail the expectation; fixed cases must pass.
    This prevents accidentally teaching the suite that any crash is correct.
    """
    if bucket not in _BUCKETS:
        raise ValueError("bucket must be known_bad or fixed")
    _validate_expected(expected)
    dump = load_case(dump_path)
    if "expected" in dump:
        raise ValueError("Input is already a regression fixture")
    observed = replay_case(dump)
    accepted = matches_expected({**dump, "expected": dict(expected)}, observed)
    if bucket == "fixed" and not accepted:
        raise ValueError("Cannot mark a failing expectation fixed")
    if bucket == "known_bad" and accepted:
        raise ValueError("Already matches expectation: use bucket=fixed")

    operation, inputs = _validate_input(dump)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "kind": "regression_fixture",
        "case_id": case_digest(dump),
        "operation": operation,
        "input": inputs,
        "expected": dict(expected),
    }
    content = json.dumps(identity, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
    if len(content) > MAX_CASE_BYTES:
        raise ValueError("Fixture exceeds maximum case file size")

    root = Path(corpus)
    directory = root / bucket
    if root.is_symlink() or directory.is_symlink():
        raise ValueError("Fixture output directory may not be a symlink")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Fixture output directory may not be a symlink")
    path = directory / f"{operation}__{identity['case_id'][:16]}.json"

    # Atomic no-clobber publishing; a conflicting expectation needs review.
    fd, tmpname = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmpname, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != content:
                raise FileExistsError("Conflicting fixture already exists") from None
    finally:
        os.unlink(tmpname)
    return path
