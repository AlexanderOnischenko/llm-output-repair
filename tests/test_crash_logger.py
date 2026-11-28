"""Opt-in local crash capture: no implicit storage or network activity."""
import json
import os
import stat

import pytest
import yaml

from llm_output_repair import CrashLogger, parse_json, repair, repair_json, repair_yaml


def _dumps(folder):
    return sorted(folder.glob("*.json"))


def test_disabled_by_default_does_not_create_any_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        repair('{"first":1} {"second":2}')
    assert list(tmp_path.iterdir()) == []


def test_explicit_folder_and_terminal_json_failure(tmp_path):
    folder = tmp_path / "only-my-crashes"
    logger = CrashLogger(folder)
    assert folder.is_dir()
    raw = '{"first":1} {"second":2}'
    with pytest.raises(ValueError):
        repair(raw, crash_logger=logger)
    dump_files = _dumps(folder)
    assert len(dump_files) == 1
    payload = json.loads(dump_files[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["format"] == "json"
    assert payload["policy"] == "safe"
    assert payload["raw_output"] == raw
    assert payload["error_type"] == "ValueError"
    assert payload["input_chars"] == len(raw)
    assert len(payload["input_sha256"]) == 64
    assert payload["captured_at"].endswith("Z")
    assert payload["truncated"] is False
    assert "error_message" not in payload


def test_successful_recovery_does_not_write(tmp_path):
    logger = CrashLogger(tmp_path / "crashes")
    assert repair('{"x":1,}', crash_logger=logger) == {"x": 1}
    assert repair('{"x":1}', crash_logger=logger) == {"x": 1}
    assert parse_json('{"x":1,}', crash_logger=logger).value == {"x": 1}
    assert not _dumps(logger.directory)


def test_parse_json_direct_and_legacy_json_capture_once_each(tmp_path):
    logger = CrashLogger(tmp_path / "crashes")
    with pytest.raises(ValueError):
        parse_json('{"x":1} {"y":2}', crash_logger=logger)
    assert len(_dumps(logger.directory)) == 1
    with pytest.raises(Exception):
        repair_json("not JSON", policy="tolerant", crash_logger=logger)
    assert len(_dumps(logger.directory)) == 2


def test_yaml_strict_records_crash_and_original_exception(tmp_path):
    logger = CrashLogger(tmp_path / "crashes")
    raw = "items: [one\n"
    with pytest.raises(yaml.YAMLError):
        repair_yaml(raw, policy="strict", crash_logger=logger)
    payload = json.loads(_dumps(logger.directory)[0].read_text())
    assert payload["format"] == "yaml"
    assert payload["policy"] == "strict"
    assert payload["raw_output"] == raw
    assert len(_dumps(logger.directory)) == 1


def test_multiple_failures_use_unique_atomic_files(tmp_path):
    logger = CrashLogger(tmp_path / "crashes")
    for _ in range(3):
        with pytest.raises(ValueError):
            repair('{"a":1} {"b":2}', crash_logger=logger)
    assert len(_dumps(logger.directory)) == 3
    assert list(logger.directory.glob("*.tmp")) == []


def test_size_cap_marks_non_replayable_dump(tmp_path):
    logger = CrashLogger(tmp_path / "crashes", max_chars=5)
    with pytest.raises(ValueError):
        repair("broken input", crash_logger=logger)
    payload = json.loads(_dumps(logger.directory)[0].read_text())
    assert payload["raw_output"] == "broke"
    assert payload["input_chars"] == 12
    assert payload["truncated"] is True


def test_bad_recording_does_not_mask_original_error(tmp_path, monkeypatch, caplog):
    logger = CrashLogger(tmp_path / "crashes")
    def fail_record(*args, **kwargs):
        raise OSError("private/path/token")
    monkeypatch.setattr(logger, "record", fail_record)
    with pytest.raises(ValueError, match="Ambiguous"):
        repair('{"a":1} {"b":2}', crash_logger=logger)
    assert "private/path/token" not in caplog.text
    assert "OSError" in caplog.text


def test_directory_explicit_and_private_permissions(tmp_path):
    with pytest.raises(ValueError):
        CrashLogger("")
    with pytest.raises(ValueError):
        CrashLogger(tmp_path / "bad", max_chars=0)
    logger = CrashLogger(tmp_path / "private")
    with pytest.raises(ValueError):
        repair('{"a":1} {"b":2}', crash_logger=logger)
    if os.name == "posix":
        assert stat.S_IMODE(logger.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(_dumps(logger.directory)[0].stat().st_mode) == 0o600
