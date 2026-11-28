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

## CrashLogger diagnostics

For diagnostic dumps in an explicitly chosen directory, instantiate
a `CrashLogger` and pass it to a parser. For the replay and regression CLI,
use `CrashRecorder` as described below.

```python
from llm_output_repair import CrashLogger, repair

crashes = CrashLogger("./private/parser-crashes")  # your own directory

try:
    result = repair('{"a":1} {"a":2}', crash_logger=crashes)
except ValueError:
    pass  # a local JSON dump was written before the original error was raised
```

`crash_logger=` is supported by `repair()`, `repair_json()`, `repair_yaml()`
and `parse_json()`. No additional dependencies, environment variables,
global singleton or background service are needed. Only **terminal parsing
failures** are saved; a failed initial attempt that ultimately recovers does
not produce a crash dump. The original parsing exception is always re-raised.
An inability to save a dump produces a sanitized warning without hiding it.

Each file contains `schema_version`, UTC timestamp, format, policy,
error class, the **raw LLM output**, original input length, SHA-256 fingerprint
and a `truncated` flag. The default `max_chars=1_000_000` limits the captured
raw payload; truncated dumps cannot be faithfully replayed. Example:

```python
crashes = CrashLogger("/path/only/you/choose", max_chars=100_000)
```

**Privacy:** raw outputs may include API keys, personal data, confidential
documents and prompt content. CrashLogger **does not anonymize, redact or
upload** anything. Newly created directories and files have restrictive
permissions on POSIX (0700 and 0600); permissions of a pre-existing directory
are not modified. Protect this directory, add it to your application's
`.gitignore`, and review dumps before sharing them. There is deliberately no
GitHub uploader or public dataset integration.

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
- Crash capture is opt-in and all dumps stay local.

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
Runtime dependencies are `PyYAML`, `pydantic` and `json-repair`.

See [architecture and compatibility](docs/architecture.md) and
[copyable examples](examples/quickstart.py).

## Development

~~~bash
pip install -e '.[test]'
python -m pytest -q
~~~

CI tests Python 3.11–3.13 and builds a wheel.

## Opt-in local crash dumps (P0)

Explicitly constructing `CrashRecorder()` uses the shared per-user dump
directory. On macOS it is
`~/Library/Application Support/LLM-output-repair/dumps`. On Linux it is
`~/.local/state/LLM-output-repair/dumps`, and on Windows it is
`%LOCALAPPDATA%/LLM-output-repair/dumps`. Pass `directory` to override it.

Crash capture is **disabled by default**: importing the package or calling a
parser without a recorder creates no dump directory, performs no upload, and
sends no telemetry.

To retain *final* failures in the shared dump directory, pass a recorder to the
public parsing functions:

~~~python
from llm_output_repair import CrashRecorder, repair

recorder = CrashRecorder(
    capture="failed",
    source={"project": "fetchgraph"},
    max_dump_bytes=1_000_000,
    max_total_bytes=100_000_000,
)

# The same recorder can be reused across calls and workers.
result = repair(raw_llm_output, recorder=recorder)
# recorder= also works with parse_json(), repair_json() and repair_yaml().
~~~

If the call fails after repair attempts, one JSON dump is saved with
`schema_version=1`, the *exact original input*, operation and parse options,
library version, UTC timestamp, exception **type** (not exception text or
traceback) and optional caller-supplied `source` labels.
Repeated identical inputs/operations/options are deduplicated. The recorder
stores dumps locally for review and replay.

**Security:** The raw LLM output may contain secrets or personal information.
Use a location *outside Git*, keep it under your control, and never upload
unreviewed dumps. New directories are created with private permissions
(`0700` on POSIX), files with `0600`. An existing directory with
group/other permissions or a symlink is rejected. Add the directory to your
application's `.gitignore` if inside a repository.

Quota exhaustion, invalid dump serialization or a filesystem failure skips
capture without changing the parser result or exception. Oversized inputs
are **not truncated** into misleading replay fixtures. The existing
`on_rejected` callback retains its behavior and may run on intermediate
rejections even when the final parse succeeds; the recorder saves only final
errors. Direct calls to legacy `extract_json_new` /
`extract_yaml_new` are not instrumented; use the public repair
wrappers above.

## Local regression workflow (P2)

A crash dump records an observed failure, *not* expected behavior. Promote it
to a regression fixture only after reviewing and explicitly supplying an
expectation. These commands are local-only and never invoke an LLM.

~~~bash
# Shared crash dump directory on macOS.
DUMPS_DIR="$HOME/Library/Application Support/LLM-output-repair/dumps"
DUMP="$DUMPS_DIR/<sha256>.json"

# Reproduce the exact saved parser call.
llm-repair replay "$DUMP"

# Prepare the expected JSON result separately from the failed output.
EXPECTED="$(mktemp)"
printf '%s\n' '{"a":1}' > "$EXPECTED"

# Create an actionable failing case.
llm-repair promote "$DUMP" \
  --corpus ./tests/regression_corpus \
  --bucket known_bad \
  --expect-json-file "$EXPECTED"

# Or specify an intentional rejection:
llm-repair promote "$DUMP" \
  --corpus ./tests/regression_corpus \
  --bucket fixed \
  --expect-error ValueError

# Verify checked corpus cases. Unresolved known_bad returns exit code 1.
llm-repair verify --corpus ./tests/regression_corpus --bucket known_bad
llm-repair verify --corpus ./tests/regression_corpus --bucket fixed
~~~

Fixtures are data-only JSON files stored at
`<corpus>/known_bad/*.json` and `<corpus>/fixed/*.json`.
The expected outcome is either the exact JSON value (`accepted`) or an
exception type (`rejected`). The parser is executed locally:
a case can only be marked `fixed` if it already matches its expectation,
and only `known_bad` if it currently fails. Existing conflicting
fixtures are never overwritten.

The pytest regression runner picks up reviewed fixtures in
`tests/regression_corpus/fixed` automatically. Known-bad failures
do not fail normal CI. The CLI reports only outcome, case ID and fixture name,
never raw model content, repaired output or exception messages. Verify returns
nonzero for an empty selected corpus rather than declaring a false success.

**Privacy:** No anonymization or uploading is implemented. Raw dumps and the
promoted fixtures both contain exact original input; keep them outside public
Git repos until deliberately reviewed. The CLI is not a sandbox for arbitrary
adversarial parser inputs; run untrusted fixtures with resource limits.

**Project status:** experimental, version 0.1.0. No PyPI release yet.
No license has been selected. Do not assume permission to redistribute or modify
the code beyond what applicable law permits.
