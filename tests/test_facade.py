"""Contract tests for the product-facing facade and legacy compatibility."""
import json

import pytest
import yaml

from llm_output_repair import (
    JsonResult,
    extract_json_new,
    parse_json,
    repair,
    repair_json,
    repair_yaml,
)


def test_repair_defaults_to_safe_json_and_preserves_content():
    assert repair('{"name":"Бухгалтер","items":[1,2,],}') == {
        "name": "Бухгалтер", "items": [1, 2]
    }
    with pytest.raises(ValueError):
        repair('{"a":1} {"b":2}')
    with pytest.raises(ValueError):
        repair('{"a":1,"a":2}')


def test_explicit_json_modes_and_root_types():
    assert repair('{"a":1}', policy="strict") == {"a": 1}
    assert repair('[1,2]', policy="strict") == [1, 2]
    with pytest.raises(ValueError):
        repair('{"a":1,}', policy="strict")
    with pytest.raises(ValueError):
        repair('[1,2]', policy="safe")
    assert repair('noise {"a":1} tail', policy="tolerant") == {"a": 1}


def test_named_json_entrypoint_preserves_legacy_default():
    assert repair_json('noise {"x":1} tail') == {"x": 1}
    assert repair_json('{"x":1,}', policy="safe") == {"x": 1}
    assert repair_json('[true,false]', policy="strict") == [True, False]
    assert json.loads(extract_json_new('{"x":1}')) == {"x": 1}


def test_repair_yaml_and_strict_yaml():
    source = "items:\n- name: example\n"
    assert repair(source, format="yaml") == {"items": [{"name": "example"}]}
    assert repair_yaml(source) == {"items": [{"name": "example"}]}
    assert repair_yaml("items:\n  - name: example\n", policy="strict") == {
        "items": [{"name": "example"}]
    }
    with pytest.raises(yaml.YAMLError):
        repair_yaml("items: [one\n", policy="strict")
    with pytest.raises(ValueError, match="only"):
        repair(source, format="yaml", policy="safe")


def test_invalid_inputs_and_dispatch():
    with pytest.raises(TypeError):
        repair(None)
    with pytest.raises(TypeError):
        repair_yaml(None)
    with pytest.raises(ValueError, match="Unsupported format"):
        repair("{}", format="xml")
    with pytest.raises(ValueError, match="Unknown JSON"):
        repair("{}", policy="other")
    with pytest.raises(ValueError, match="YAML"):
        repair_yaml("key: value", policy="other")


def test_diagnostic_api_remains_accessible():
    details = parse_json('{"a":1,}', policy="safe")
    assert isinstance(details, JsonResult)
    assert details.value == {"a": 1}
    assert details.repaired is True
    assert details.strategy == "safe"
