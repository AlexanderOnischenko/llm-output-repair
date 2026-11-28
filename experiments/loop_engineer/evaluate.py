"""Frozen-corpus, offline evaluator for a bounded Loop Engineer experiment.

No crash dumps or fixture contents are printed or uploaded. The host supplies
an explicitly reviewed private corpus; this tool does not create expectations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from llm_output_repair import __version__
from llm_output_repair.regression import (
    case_digest,
    fixture_paths,
    load_case,
    matches_expected,
    replay_case,
)

SCHEMA = "llm-repair-loop/v1"


def _canonical(data: Any) -> bytes:
    return json.dumps(
        data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Input must be a regular, non-symlink file")
    if path.stat().st_size > 16_000_000:
        raise ValueError("Input file is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _save(path: Path, data: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError("Refusing to overwrite existing experiment evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("Evidence directory must not be a symlink")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")


def inventory(corpus: Path) -> list[dict[str, str]]:
    """Get a deterministic manifest of reviewed fixtures, without their raw text."""
    if corpus.is_symlink() or not corpus.is_dir():
        raise ValueError("Corpus must be a real directory")
    entries: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for bucket in ("known_bad", "fixed"):
        for path in fixture_paths(corpus, bucket=bucket):
            if path.is_symlink() or not path.is_file():
                raise ValueError("Fixture must be a regular file, not a symlink")
            case = load_case(path)
            if case.get("kind") != "regression_fixture" or "expected" not in case:
                raise ValueError("Unqualified crash dump is not a regression fixture")
            digest = case_digest(case)
            if digest in seen_ids:
                raise ValueError("Duplicate case in experiment corpus")
            seen_ids.add(digest)
            entries.append({
                "bucket": bucket,
                "filename": path.name,
                "case_id": digest,
                "sha256": _hash(path.read_bytes()),
                "expected": case["expected"]["status"],
            })
    entries.sort(key=lambda value: (value["bucket"], value["filename"]))
    return entries


def snapshot(corpus: Path) -> dict[str, Any]:
    """Freeze a fingerprint and cohort membership before either agent runs."""
    cases = inventory(corpus)
    known_bad = sum(e["bucket"] == "known_bad" for e in cases)
    fixed = sum(e["bucket"] == "fixed" for e in cases)
    rejected = sum(
        e["bucket"] == "fixed" and e["expected"] == "rejected" for e in cases
    )
    if not known_bad or not fixed or not rejected:
        raise ValueError(
            "Need >=1 reviewed known_bad, >=1 fixed, and >=1 fixed rejection guardrail"
        )
    manifest = {"schema": SCHEMA, "cases": cases}
    return {**manifest, "fingerprint": _hash(_canonical(manifest))}


def _git_sha() -> str:
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, timeout=5
        ).stdout.strip()
        return value or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def evaluate(corpus: Path, manifest: dict[str, Any], arm: str) -> dict[str, Any]:
    if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("cases"), list):
        raise ValueError("Invalid experiment manifest")
    computed = snapshot(corpus)
    if computed != manifest:
        raise ValueError("Frozen corpus changed; refusing incomparable run")
    records: list[dict[str, Any]] = []
    started = time.perf_counter()
    for entry in manifest["cases"]:
        # The input is a reviewed fixture, never an executable file.
        path = corpus / entry["bucket"] / entry["filename"]
        case = load_case(path)
        case_started = time.perf_counter()
        observed = replay_case(case)
        matched = matches_expected(case, observed)
        records.append({
            "case_id": entry["case_id"],
            "bucket": entry["bucket"],
            "expected": entry["expected"],
            "observed": observed.status,
            "matches_expected": matched,
            "elapsed_ms": round((time.perf_counter() - case_started) * 1000, 2),
        })
    fixed = [e for e in records if e["bucket"] == "fixed"]
    known_bad = [e for e in records if e["bucket"] == "known_bad"]
    guardrails = [e for e in fixed if e["expected"] == "rejected"]
    return {
        "schema": SCHEMA,
        "kind": "evaluation",
        "arm": arm,
        "corpus_fingerprint": manifest["fingerprint"],
        "code_sha": _git_sha(),
        "library_version": __version__,
        "python_version": sys.version.split()[0],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "summary": {
            "cases": len(records),
            "fixed_pass": sum(e["matches_expected"] for e in fixed),
            "fixed_total": len(fixed),
            "negative_pass": sum(e["matches_expected"] for e in guardrails),
            "negative_total": len(guardrails),
            "known_bad_resolved": sum(e["matches_expected"] for e in known_bad),
            "known_bad_total": len(known_bad),
        },
        "cases": records,
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if (
        baseline.get("schema") != SCHEMA
        or candidate.get("schema") != SCHEMA
        or baseline.get("kind") != "evaluation"
        or candidate.get("kind") != "evaluation"
        or baseline.get("corpus_fingerprint") != candidate.get("corpus_fingerprint")
    ):
        raise ValueError("Incomparable evaluations or different frozen corpora")
    before = {e["case_id"]: e for e in baseline["cases"]}
    after = {e["case_id"]: e for e in candidate["cases"]}
    if before.keys() != after.keys():
        raise ValueError("Different cases in baseline and candidate")
    for case_id in before:
        if (
            before[case_id]["bucket"] != after[case_id]["bucket"]
            or before[case_id]["expected"] != after[case_id]["expected"]
        ):
            raise ValueError("Case or oracle definition changed")
    fixed_losses = sorted(
        case_id for case_id, value in before.items()
        if value["bucket"] == "fixed" and value["matches_expected"]
        and not after[case_id]["matches_expected"]
    )
    new_fixes = sorted(
        case_id for case_id, value in before.items()
        if value["bucket"] == "known_bad" and not value["matches_expected"]
        and after[case_id]["matches_expected"]
    )
    reopened = sorted(
        case_id for case_id, value in before.items()
        if value["bucket"] == "known_bad" and value["matches_expected"]
        and not after[case_id]["matches_expected"]
    )
    neg = [
        value for value in after.values()
        if value["bucket"] == "fixed" and value["expected"] == "rejected"
    ]
    fixed = [value for value in after.values() if value["bucket"] == "fixed"]
    eligible = (
        bool(new_fixes)
        and not fixed_losses
        and not reopened
        and all(e["matches_expected"] for e in neg)
        and all(e["matches_expected"] for e in fixed)
    )
    return {
        "schema": SCHEMA,
        "kind": "comparison",
        "corpus_fingerprint": baseline["corpus_fingerprint"],
        "baseline_sha": baseline["code_sha"],
        "candidate_sha": candidate["code_sha"],
        "newly_resolved": len(new_fixes),
        "fixed_regressions": len(fixed_losses),
        "reopened_known_bad": len(reopened),
        "negative_guardrail_passed": all(e["matches_expected"] for e in neg),
        "eligible_first_success": eligible,
        "case_ids": {
            "newly_resolved": new_fixes,
            "fixed_regressions": fixed_losses,
            "reopened_known_bad": reopened,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline frozen-corpus experiment")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot")
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("run")
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--arm", required=True, choices=["baseline", "control", "loop"])
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            record = snapshot(args.corpus)
        elif args.command == "run":
            record = evaluate(args.corpus, _read(args.manifest), args.arm)
        else:
            record = compare(_read(args.baseline), _read(args.candidate))
        _save(args.out, record)
        if args.command == "snapshot":
            print(json.dumps({
                "status": "frozen", "fingerprint": record["fingerprint"],
                "cases": len(record["cases"])
            }, sort_keys=True))
            return 0
        if args.command == "run":
            print(json.dumps(record["summary"], sort_keys=True))
            return 0
        print(json.dumps({
            "eligible_first_success": record["eligible_first_success"],
            "newly_resolved": record["newly_resolved"],
            "fixed_regressions": record["fixed_regressions"],
            "reopened_known_bad": record["reopened_known_bad"],
        }, sort_keys=True))
        return 0 if record["eligible_first_success"] else 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(f"experiment: {type(exc).__name__}; no evidence written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
