"""Harness contract tests; synthetic fixtures are NOT product bug reports."""
import json
from pathlib import Path

import pytest

from llm_output_repair.regression import case_digest
from experiments.loop_engineer import evaluate as experiment


def _fixture(root: Path, bucket: str, raw: str, expected: dict, name: str):
    obj = {
        "schema_version": 1,
        "kind": "regression_fixture",
        "operation": "parse_json",
        "input": {
            "raw": raw,
            "options": {"policy": "strict", "expected_type": "dict"},
        },
        "expected": expected,
    }
    obj["case_id"] = case_digest(obj)
    folder = root / bucket
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + ".json")
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _corpus(tmp_path):
    corpus = tmp_path / "private"
    _fixture(
        corpus, "fixed", '{"a":1}',
        {"status":"accepted", "value":{"a":1}}, "accepted"
    )
    _fixture(
        corpus, "fixed", '{"a":1,}',
        {"status":"rejected", "exception_type":"JSONDecodeError"}, "negative"
    )
    # Synthetic, deliberately impossible oracle tests harness handling only.
    _fixture(
        corpus, "known_bad", '{"lost":',
        {"status":"accepted", "value":{"lost":1}}, "synthetic_unresolved"
    )
    return corpus


def test_manifest_and_baseline(tmp_path):
    corpus = _corpus(tmp_path)
    manifest = experiment.snapshot(corpus)
    assert len(manifest["cases"]) == 3
    report = experiment.evaluate(corpus, manifest, "baseline")
    assert report["summary"] == {
        "cases": 3, "fixed_pass": 2, "fixed_total": 2,
        "negative_pass": 1, "negative_total": 1,
        "known_bad_resolved": 0, "known_bad_total": 1,
    }
    assert report["corpus_fingerprint"] == manifest["fingerprint"]


def test_corpus_drift_is_rejected(tmp_path):
    corpus = _corpus(tmp_path)
    frozen = experiment.snapshot(corpus)
    target = next((corpus / "fixed").glob("*.json"))
    target.write_text(target.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen corpus changed"):
        experiment.evaluate(corpus, frozen, "loop")


def test_missing_negative_or_known_bad_blocks_trial(tmp_path):
    corpus = _corpus(tmp_path)
    next((corpus / "fixed").glob("negative.json")).unlink()
    with pytest.raises(ValueError, match="Need"):
        experiment.snapshot(corpus)


def test_comparison_detects_success_and_regressions(tmp_path):
    corpus = _corpus(tmp_path)
    manifest = experiment.snapshot(corpus)
    before = experiment.evaluate(corpus, manifest, "baseline")
    after = json.loads(json.dumps(before))
    after["arm"] = "loop"
    for case in after["cases"]:
        if case["bucket"] == "known_bad":
            case["matches_expected"] = True
    comparison = experiment.compare(before, after)
    assert comparison["eligible_first_success"] is True
    assert comparison["newly_resolved"] == 1
    for case in after["cases"]:
        if case["bucket"] == "fixed":
            case["matches_expected"] = False
            break
    comparison = experiment.compare(before, after)
    assert comparison["eligible_first_success"] is False
    assert comparison["fixed_regressions"] == 1


def test_empty_and_duplicate_workloads_are_not_accepted(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="Need"):
        experiment.snapshot(empty)
    corpus = _corpus(tmp_path)
    item = next((corpus / "fixed").glob("accepted.json"))
    clone = corpus / "known_bad" / "duplicate.json"
    clone.write_bytes(item.read_bytes())
    with pytest.raises(ValueError, match="Duplicate"):
        experiment.snapshot(corpus)


def test_cli_cannot_overwrite_existing_evidence(tmp_path):
    corpus = _corpus(tmp_path)
    out = tmp_path / "manifest.json"
    assert experiment.main(["snapshot", "--corpus", str(corpus), "--out", str(out)]) == 0
    assert experiment.main(["snapshot", "--corpus", str(corpus), "--out", str(out)]) == 2


def test_comparison_refuses_different_oracles(tmp_path):
    corpus = _corpus(tmp_path)
    manifest = experiment.snapshot(corpus)
    before = experiment.evaluate(corpus, manifest, "baseline")
    after = json.loads(json.dumps(before))
    after["corpus_fingerprint"] = "changed"
    with pytest.raises(ValueError, match="Incomparable"):
        experiment.compare(before, after)
