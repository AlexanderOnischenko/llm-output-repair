# Architecture and compatibility

## One package, two layers

- `api.py` defines the stable, small application-facing interface: `repair`,
  `repair_json` and `repair_yaml`. It owns format/policy dispatch and input validation.
- `controlled_json.py` implements conservative JSON decoding and diagnostics
  (`parse_json`, `JsonResult`), including strict-first behavior and token checks.
- `extract_json.py` and `extract_yaml.py` are inherited heuristic engines.
  They remain importable, and their helper signatures are not migrated in this
  productization step.
- `yaml_normalizer.py` and `yaml_transport.py` preserve specialized normalization
  and transport helpers.
- `__init__.py` explicitly exports supported entry points. Tests cover their
  intended behavior, and CI builds a redistributable wheel.

No runtime import points at Backplane, a client, a private prompt or an LLM service.
Packages needed at runtime are declared in `pyproject.toml`. Pydantic support for
`dump_yaml` is optional and imported only when installed.

## Default policy decisions

- New `repair(..., format="json")`: SAFE JSON, object-root only.
- Existing `repair_json(...)`: legacy TOLERANT, to avoid breaking users.
- New and existing YAML entry points: TOLERANT by default, no SAFE YAML claim.
- Explicit `strict`: parse without repair. JSON uses duplicate-key and non-finite
  checks; YAML uses PyYAML's safe loader.

`parse_json(..., policy="safe")` is still available for callers who need a
`JsonResult` with recovery metadata. Domain validation remains caller-owned.

## Error behavior

Invalid format/policy raises `ValueError`; non-string input raises `TypeError`.
Parsing failures propagate errors from the underlying parsers. The library does
not store or transmit rejected data. Callers should not log raw exceptions or
payloads indiscriminately because some parser errors include input excerpts.

## Compatibility contract

No breaking renames or moves of existing modules in this step. In particular,
`extract_json_new` continues to return a JSON-formatted **string** rather than
a decoded object, and `extract_yaml_new` returns a decoded Python value.

Future changes should include regression tests for both layers. Only promote
heuristics into SAFE when their accepted edits can be checked independently.

## Optional crash capture

`crash_logger.py` provides an explicit, caller-instantiated `CrashLogger`.
No global logger, default path, background task, anonymizer or upload service
exists. The public facade and `controlled_json.parse_json` record only
unrecoverable failures, and do so only when a logger is passed.
Nested facade calls never double-record a single failure.

Crash dumps are versioned JSON documents suitable as a future source for
local regression tests. The replay contract should reject truncated dumps and
must validate expected outcomes separately from the raw payload. Uploading
or publishing dumps needs independent user review and explicit consent.
