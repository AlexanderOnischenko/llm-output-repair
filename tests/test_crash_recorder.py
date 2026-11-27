"""P0 opt-in crash capture: no implicit I/O, no changes to parse semantics."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from llm_output_repair import CrashRecorder, parse_json, repair_json, repair_yaml

BAD_JSON = ' \n{"a": 1'  # deliberately incomplete; preserve leading whitespace


def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        parse_json(BAD_JSON)
    assert list(tmp_path.iterdir()) == []


def test_dump_has_exact_raw_options_and_no_exception_message(tmp_path):
    recorder = CrashRecorder(tmp_path / "private", source={"project": "fetchgraph"})
    with pytest.raises(ValueError):
        parse_json(BAD_JSON, policy="safe", recorder=recorder)
    files = list(recorder.directory.glob("*.json"))
    assert len(files) == 1
    dump = json.loads(files[0].read_text("utf-8"))
    assert dump["schema_version"] == 1
    assert dump["operation"] == "parse_json"
    assert dump["input"] == {
        "raw": BAD_JSON,
        "options": {"policy": "safe", "expected_type": "dict"},
    }
    assert dump["observed"] == {"status": "failed", "exception_type": "ValueError"}
    assert dump["library_version"]
    assert dump["captured_at"]
    assert dump["source"] == {"project": "fetchgraph"}

    path = recorder.record_failure(
        operation="repair_json", raw="bad-input", error=RuntimeError("PRIVATE_ERROR_MESSAGE")
    )
    assert path is not None
    assert "PRIVATE_ERROR_MESSAGE" not in path.read_text("utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(recorder.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_successful_repair_and_strict_success_are_not_captured(tmp_path):
    recorder = CrashRecorder(tmp_path / "dumps")
    assert parse_json('{"a":1,}', recorder=recorder).value == {"a": 1}
    assert parse_json('{"a":1}', recorder=recorder).value == {"a": 1}
    assert repair_json('{"a":1}', recorder=recorder) == {"a": 1}
    assert repair_yaml('a: 1\n', recorder=recorder) == {"a": 1}
    assert not list(recorder.directory.glob("*.json"))


def test_legacy_public_helpers_capture_failures(tmp_path):
    recorder = CrashRecorder(tmp_path / "dumps")
    with patch("llm_output_repair.extract_json_new", side_effect=RuntimeError("private")):
        with pytest.raises(RuntimeError, match="private"):
            repair_json("  raw json  ", recorder=recorder)
    with patch("llm_output_repair.extract_yaml_new", side_effect=RuntimeError("private")):
        with pytest.raises(RuntimeError, match="private"):
            repair_yaml("  raw yaml  ", recorder=recorder)
    dumps = [json.loads(p.read_text("utf-8")) for p in recorder.directory.glob("*.json")]
    assert {d["operation"] for d in dumps} == {"repair_json", "repair_yaml"}
    assert {d["input"]["raw"] for d in dumps} == {"  raw json  ", "  raw yaml  "}


def test_recording_exception_never_masks_parser_exception(tmp_path, monkeypatch):
    recorder = CrashRecorder(tmp_path / "dumps")

    def fail_to_write(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "record_failure", fail_to_write)
    with pytest.raises(json.JSONDecodeError):
        parse_json(BAD_JSON, policy="strict", recorder=recorder)
    assert not list(recorder.directory.glob("*.json"))


def test_duplicates_are_atomic_under_concurrent_writers(tmp_path):
    directory = tmp_path / "dumps"
    recorder_one = CrashRecorder(directory)
    recorder_two = CrashRecorder(directory)

    def run(index):
        rec = recorder_one if index % 2 else recorder_two
        return rec.record_failure(
            operation="parse_json", raw=BAD_JSON, error=ValueError("bad"),
            options={"policy": "safe", "expected_type": "dict"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(run, range(24)))
    assert all(path is not None for path in paths)
    assert len(set(paths)) == 1
    assert len(list(directory.glob("*.json"))) == 1
    assert not list(directory.glob(".pending-*"))
    assert json.loads(paths[0].read_text("utf-8"))["input"]["raw"] == BAD_JSON


def test_size_and_total_quotas(tmp_path):
    tiny = CrashRecorder(tmp_path / "tiny", max_dump_bytes=128)
    assert tiny.record_failure(operation="repair_json", raw=BAD_JSON, error=ValueError()) is None
    assert not list(tiny.directory.glob("*.json"))

    directory = tmp_path / "limited"
    first = CrashRecorder(directory).record_failure(
        operation="repair_json", raw="bad-1", error=ValueError()
    )
    assert first is not None
    limit = first.stat().st_size + 8
    limited = CrashRecorder(directory, max_total_bytes=limit)
    assert limited.record_failure(operation="repair_json", raw="bad-2", error=ValueError()) is None
    assert list(directory.glob("*.json")) == [first]
    # The same payload does not consume the quota twice.
    assert limited.record_failure(operation="repair_json", raw="bad-1", error=ValueError()) == first


def test_reject_insecure_existing_directory_or_symlink(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX permissions")
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(PermissionError):
        CrashRecorder(shared)
    link = tmp_path / "link"
    link.symlink_to(shared, target_is_directory=True)
    with pytest.raises(ValueError):
        CrashRecorder(link)


def test_invalid_configuration_fails_early(tmp_path):
    with pytest.raises(ValueError):
        CrashRecorder(tmp_path / "dumps", capture="all")
    with pytest.raises(ValueError):
        CrashRecorder(tmp_path / "dumps", max_dump_bytes=0)
    with pytest.raises(ValueError):
        CrashRecorder(tmp_path / "dumps", source={"case": 99})
