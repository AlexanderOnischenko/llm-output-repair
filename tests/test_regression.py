"""P2 data-only crash replay and expectation-based regression tests."""
import json
from pathlib import Path
import pytest
from llm_output_repair import CrashRecorder, parse_json
from llm_output_repair.cli import main
from llm_output_repair.regression import (
    case_digest, fixture_paths, load_case, matches_expected,
    promote_dump, replay_case, Observation,
)

def make_dump(tmp_path, raw='{"a":', policy="safe"):
    recorder = CrashRecorder(tmp_path / "private")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_json(raw, policy=policy, recorder=recorder)
    dumps = list(recorder.directory.glob("*.json"))
    assert len(dumps) == 1
    return dumps[0]

def test_replay_exact_input_without_model(tmp_path):
    dump = make_dump(tmp_path)
    case = load_case(dump)
    result = replay_case(case)
    assert result.status == "rejected"
    assert result.exception_type == "ValueError"
    assert case_digest(case) == dump.stem

def test_promote_known_bad_and_verify(tmp_path, capsys):
    dump = make_dump(tmp_path)
    expected = tmp_path / "expected.json"
    expected.write_text('{"a":1}', encoding="utf-8")
    corpus = tmp_path / "corpus"
    assert main(["promote", str(dump), "--corpus", str(corpus), "--expect-json-file", str(expected)]) == 0
    capsys.readouterr()
    paths = list(fixture_paths(corpus, "known_bad"))
    assert len(paths) == 1
    fixture = load_case(paths[0])
    assert fixture["expected"] == {"status": "accepted", "value": {"a": 1}}
    assert not matches_expected(fixture, replay_case(fixture))
    assert main(["verify", "--corpus", str(corpus), "--bucket", "known_bad"]) == 1
    assert '"failed": 1' in capsys.readouterr().out
    assert main(["verify", "--corpus", str(corpus)]) == 1
    assert '"total": 0' in capsys.readouterr().out

def test_fixed_and_deterministic_replay(tmp_path):
    dump = make_dump(tmp_path)
    corpus = tmp_path / "corpus"
    expectation = {"status":"rejected","exception_type":"ValueError"}
    fixture = promote_dump(dump, corpus=corpus, bucket="fixed", expected=expectation)
    assert matches_expected(load_case(fixture), replay_case(load_case(fixture)))
    assert main(["verify", "--corpus", str(corpus)]) == 0
    assert main(["replay", str(fixture)]) == 0
    with pytest.raises(ValueError, match="Already matches"):
        promote_dump(dump, corpus=corpus, expected=expectation)

def test_cli_never_prints_raw(tmp_path, capsys):
    secret = "PRIVATE_CUSTOMER_123"
    dump = make_dump(tmp_path, raw=secret)
    assert main(["replay", str(dump)]) == 1
    out = capsys.readouterr()
    assert secret not in (out.out + out.err)

def test_dedup_and_conflicting_expectations(tmp_path):
    dump = make_dump(tmp_path)
    corpus = tmp_path / "corpus"
    e = {"status":"accepted","value":{"a":1}}
    assert promote_dump(dump, corpus=corpus, expected=e) == promote_dump(dump, corpus=corpus, expected=e)
    assert len(list(fixture_paths(corpus, "known_bad"))) == 1
    with pytest.raises(FileExistsError):
        promote_dump(dump, corpus=corpus, expected={"status":"accepted","value":{"a":2}})

@pytest.mark.parametrize("expected", [
    {}, {"status":"accepted"},
    {"status":"rejected","exception_type":"ValueError","value":{}},
    {"status":"rejected","exception_type":"__import__('os')"},
])
def test_reject_bad_expectations(tmp_path, expected):
    dump = make_dump(tmp_path)
    with pytest.raises(ValueError):
        promote_dump(dump, corpus=tmp_path / "corpus", expected=expected)

def test_type_aware_values(tmp_path):
    case = load_case(make_dump(tmp_path))
    case["expected"] = {"status":"accepted","value":True}
    assert not matches_expected(case, Observation(status="accepted", value=1))

def test_reject_unknown_operation_and_duplicate_keys(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version":1,"operation":"__import__","input":{"raw":"","options":{}},"observed":{"status":"failed"}}))
    with pytest.raises(ValueError):
        load_case(bad)
    assert main(["replay", str(bad)]) == 2
    bad.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_case(bad)

def test_strict_replay(tmp_path):
    case = load_case(make_dump(tmp_path, policy="strict"))
    assert replay_case(case).exception_type == "JSONDecodeError"

def test_promote_missing_expected_file(tmp_path):
    dump = make_dump(tmp_path)
    assert main(["promote", str(dump), "--corpus", str(tmp_path / "corpus"),
                 "--expect-json-file", str(tmp_path / "missing.json")]) == 2

def test_fixed_corpus_integrated_with_pytest():
    corpus = Path(__file__).resolve().parent / "regression_corpus"
    for path in fixture_paths(corpus, "fixed"):
        case = load_case(path)
        assert matches_expected(case, replay_case(case)), f"Regression: {path.name}"
