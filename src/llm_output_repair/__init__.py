"""Deterministic extraction and heuristic repair of LLM-generated JSON/YAML."""
from .extract_json import extract_json_new as extract_json_new
from .extract_yaml import extract_yaml_new as extract_yaml_new, dump_yaml as dump_yaml
from .yaml_normalizer import YamlNormalizer as YamlNormalizer
import json as _json
from typing import Any

__version__ = "0.1.0"

def repair_json(raw: str) -> Any:
    """Return a decoded value using the inherited tolerant JSON extractor."""
    return _json.loads(extract_json_new(raw))

def repair_yaml(raw: str) -> Any:
    """Return the parsed value using the inherited tolerant YAML extractor."""
    return extract_yaml_new(raw)

from .controlled_json import parse_json as parse_json, JsonResult as JsonResult
