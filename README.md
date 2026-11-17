# LLM Output Repair

**Recover structured LLM responses locally, without another model call.**

A small Python library for extracting, repairing and decoding JSON/YAML output.
It does not call an LLM, require API keys, send telemetry or depend on application code.

## Quick start

Requires Python 3.11+.

~~~bash
git clone https://github.com/AlexanderOnischenko/llm-output-repair.git
cd llm-output-repair
pip install .
~~~

This project has **not been published to PyPI**. Install from a checkout as above,
or directly from the GitHub repository using `pip install git+https://github.com/AlexanderOnischenko/llm-output-repair.git`.

~~~python
from llm_output_repair import repair

data = repair('{"name": "example", "items": [1,2,],}')
assert data == {"name": "example", "items": [1, 2]}

yaml_data = repair("items:\n- name: example\n", format="yaml")
assert yaml_data == {"items": [{"name": "example"}]}
~~~

`repair()` returns ordinary Python values (`dict`, `list`, etc.). No need to
know about `extract_json_new` or individual normalization stages.

## Choose how much repair is acceptable

| Policy | JSON | YAML |
| --- | --- | --- |
| `strict` | Decode as-is, reject invalid syntax, duplicate object keys and non-finite constants | Parse as-is via `yaml.safe_load` |
| `safe` | Conservative recovery; only supported edits are accepted; **JSON objects only** | Not supported |
| `tolerant` | Existing powerful but potentially lossy heuristic extractor | Existing heuristic normalizer |

For new integrations, **JSON defaults to `safe` and YAML defaults to `tolerant`**:

~~~python
from llm_output_repair import repair

response = repair('{"ok":true,}')                       # JSON SAFE
response = repair('noise {"ok":true} end', policy="tolerant")
response = repair('{"ok":true}', policy="strict")
response = repair("ok: true", format="yaml")              # YAML TOLERANT
response = repair("ok: true", format="yaml", policy="strict")
~~~

The named entry points are also available:

~~~python
from llm_output_repair import repair_json, repair_yaml

repair_json('noise {"x":1} tail')      # legacy tolerant default
repair_json('{"x":1,}', policy="safe")  # explicitly conservative
repair_yaml("x: 1\n")                  # legacy tolerant default
~~~

**Compatibility:** `repair_json()` and `repair_yaml()` preserve their historical
tolerant defaults. The new unified `repair()` uses SAFE for JSON.
The low-level `extract_json_new` (returns JSON **text**), `extract_yaml_new`
(returns a Python value), `dump_yaml`, `YamlNormalizer` and transport helpers
remain available for existing integrations.

For JSON recovery metadata and optional caller-owned diagnostics:

~~~python
from llm_output_repair import parse_json

result = parse_json('{"name":"example",}', policy="safe")
assert result.value == {"name": "example"}
assert result.repaired is True
print(result.strategy)  # "safe"
~~~

`parse_json` returns `JsonResult(value, repaired, strategy)`. Optional
`on_rejected` is a callback for *sanitized errors*, not raw model output.

## Safety and scope

This is a **repair library, not a validator, sandbox or security boundary**.

- Even SAFE cannot guarantee that a malformed response had only one intended meaning.
  Validate against your schema and domain invariants after repair.
- SAFE JSON rejects incomplete/ambiguous documents, duplicate keys and unsupported
  content changes. It enforces a 1,000,000-character input cap, but **does not
  guarantee hard runtime or memory bounds**.
- TOLERANT can change values or structure: it may unwrap embedded JSON, drop
  ellipses, strip surrounding prose or alter YAML indentation. It does not enforce
  resource limits. Do not use it with untrusted/unbounded input without your own limits.
- YAML does not have an equivalent SAFE repair contract. PyYAML `safe_load`
  avoids constructing arbitrary Python objects, but is **not a size/CPU limit**.
- No crash dumps, model responses or secrets are collected or uploaded by this library.

## Architecture

~~~text
Public facade (repair / repair_json / repair_yaml)
  ├── JSON: controlled_json.parse_json (STRICT / SAFE)
  │           └── extract_json (legacy fallback candidate)
  ├── JSON: extract_json (TOLERANT compatibility)
  └── YAML: PyYAML safe_load (STRICT) / extract_yaml (TOLERANT)
                                     └── yaml_normalizer / yaml_transport helpers
~~~

All runtime code lives under `src/llm_output_repair/`. No Backplane import,
application configuration, inference provider or domain-specific schema is required.
Required dependencies are `PyYAML` and `json-repair`. Install `.[pydantic]`
only if you want `dump_yaml` to serialize Pydantic models directly.

See [architecture and compatibility](docs/architecture.md) and
[copyable examples](examples/quickstart.py).

## Development

~~~bash
pip install -e '.[test]'
python -m pytest -q
~~~

CI tests Python 3.11–3.13 and builds a wheel.

**Project status:** experimental, version 0.1.0. No PyPI release yet.
No license has been selected. Do not assume permission to redistribute or modify
the code beyond what applicable law permits.
