"""Deterministic extraction and heuristic repair of LLM-generated JSON/YAML.

For new integrations, use repair(). The legacy extraction helpers remain
available for clients that depend on their original behavior.
"""
from .api import repair, repair_json, repair_yaml
from .controlled_json import JsonResult, parse_json
from .crash_logger import CrashLogger
from .extract_json import extract_json_new
from .extract_yaml import dump_yaml, extract_yaml_new
from .yaml_normalizer import YamlNormalizer

__version__ = "0.1.0"

__all__ = [
    "repair",
    "repair_json",
    "repair_yaml",
    "parse_json",
    "JsonResult",
    "CrashLogger",
    "extract_json_new",
    "extract_yaml_new",
    "dump_yaml",
    "YamlNormalizer",
]
