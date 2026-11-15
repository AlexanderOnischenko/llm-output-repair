import json
from llm_output_repair import repair_json, repair_yaml, extract_json_new, dump_yaml, YamlNormalizer
from llm_output_repair.yaml_transport import _transport_preprocess_yaml_text

def test_json_api():
    assert repair_json('noise {"x":1} tail') == {"x":1}
    assert json.loads(extract_json_new('{"x":1}')) == {"x":1}

def test_yaml_api():
    assert repair_yaml('items:\n- name: example\n') == {"items":[{"name":"example"}]}
    assert repair_yaml(dump_yaml({"name":"example"})) == {"name":"example"}
    assert "items:" in YamlNormalizer().normalize("items:\n  - example")

def test_transport_wrapper():
    assert _transport_preprocess_yaml_text('{"text":"items: example"}')[0] == "items: example"
