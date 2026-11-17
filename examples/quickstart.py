"""Run with: python examples/quickstart.py (after pip install .)."""
from llm_output_repair import parse_json, repair, repair_json

safe = repair('{"user":"Ada","ids":[1,2,],}')
assert safe == {"user": "Ada", "ids": [1, 2]}

strict = repair('{"status":"ok"}', policy="strict")
assert strict["status"] == "ok"

yaml_value = repair("status: ok\n", format="yaml")
assert yaml_value == {"status": "ok"}

# Legacy applications can request the more permissive extractor explicitly.
tolerant = repair_json('The result: {"status":"ok"}')
assert tolerant == strict

details = parse_json('{"user":"Ada",}', policy="safe")
print(details.value, details.repaired, details.strategy)
