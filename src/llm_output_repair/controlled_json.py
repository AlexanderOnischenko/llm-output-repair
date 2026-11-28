"""Controlled JSON recovery; no application or domain dependencies."""
import json
import logging
import re
from json_repair import repair_json
from .crash_logger import CrashLogger, capture_failure
from .crash_recorder import CrashRecorder, _capture_failure
from .extract_json import extract_json_new, quote_unquoted_keys, escape_control_chars_inside_strings, remove_unmatched_brackets

log = logging.getLogger(__name__)
MAX_REPAIR_CHARS = 1_000_000

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result

def _constant(value):
    raise ValueError("Non-finite JSON constant")

def _decode(text):
    return json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)

def _object_end(text):
    """Find the end of one complete outer object, respecting quoted strings."""
    if not text.startswith("{"):
        raise ValueError("Repair requires a complete JSON object")
    stack = []
    quoted = escaped = False
    for i, ch in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
            continue
        if ch == '"':
            quoted = True
        elif ch in "{[":
            stack.append(ch)
            if len(stack) > 128:
                raise ValueError("JSON repair nesting limit")
        elif ch in "}]":
            if not stack or stack.pop() != ("{" if ch == "}" else "["):
                raise ValueError("Mismatched JSON delimiters")
            if not stack:
                return i + 1
    raise ValueError("Incomplete JSON object; refusing to synthesize data")

# Compare lexical tokens other than commas. This admits comma-only repairs
# and rejects deletion, insertion, type changes and duplicate-key collapse.
_TOKEN = re.compile(r'\s+|"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\]:,]', re.DOTALL)

def _tokens(text):
    pos = 0
    result = []
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ValueError("Unsupported JSON repair token")
        token = match.group()
        pos = match.end()
        if token.isspace() or token == ",":
            continue
        if token.startswith('"'):
            result.append(("string", json.loads(token)))
        elif token[0] in "-0123456789":
            # Preserve numerical value and JSON integer/float distinction.
            value = json.loads(token)
            result.append(("number", type(value), value))
        else:
            result.append(("syntax", token))
    return result

def _conservative_parse(raw):
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.IGNORECASE)
    try:
        value = _decode(text)
    except json.JSONDecodeError as original:
        if len(text) > MAX_REPAIR_CHARS:
            raise ValueError("JSON repair input too large") from None
        end = _object_end(text)
        candidate, tail = text[:end], text[end:].strip()
        # A strictly valid prefix followed only by surplus closing delimiters
        # has one complete value. Require the repair to reproduce that prefix.
        if original.msg == "Extra data" and re.fullmatch(r"[}\]\s]+", tail):
            value = _decode(candidate)
            repaired = remove_unmatched_brackets(text)
            if repaired.strip() != candidate.strip():
                raise ValueError("Surplus delimiter repair changed document")
            log.warning("JSON recovered strategy=surplus_closing_delimiters")
            return value
        if tail:
            # Only plain prose (or a closing fence plus prose) is removable.
            # Never select one of multiple structured proposals.
            tail = re.sub(r'^```\s*', '', tail)
            if tail and (not tail[0].isalpha() or re.search(r'[{}\[\]"`]', tail)
                         or re.match(r'^(true|false|null)\b', tail, re.I)):
                raise ValueError("Ambiguous extra JSON data") from None
        try:
            value = _decode(candidate)
            strategy = "complete_object"
        except json.JSONDecodeError:
            try:
                repaired = repair_json(candidate, skip_json_loads=True, ensure_ascii=False)
                if _tokens(candidate) != _tokens(repaired):
                    raise ValueError("JSON repair changed content tokens")
                value = _decode(repaired)
            except (ValueError, TypeError, RecursionError):
                raise ValueError("JSON repair rejected: not a conservative comma repair") from None
            strategy = "json_repair"
        log.warning("Model JSON recovered strategy=%s error=%s chars=%s",
                    strategy, original.msg, len(text))
    if not isinstance(value, dict):
        raise ValueError("Invalid proposal")
    return value


# The inherited Backplane engine can perform lossy operations. It is a
# candidate generator, not an authority. Check the entire original document
# against the result, rather than accepting the first extracted JSON fragment.
def _extended_tokens(text):
    # Only key quoting, comma/colon syntax and escaping literal control
    # characters may differ. Every bracket, string value and numeric lexeme
    # must survive, in order. Never unwrap stringified nested objects.
    text = quote_unquoted_keys(text)
    pos = 0
    result = []
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ValueError("Unsupported Backplane repair token")
        token = match.group()
        pos = match.end()
        if token.isspace() or token in (",", ":"):
            continue
        if token.startswith('"'):
            result.append(("string", json.loads(token, strict=False)))
        elif token[0] in "-0123456789":
            # Exact lexeme prevents rounding or number type changes.
            result.append(("number", token))
        else:
            result.append(("syntax", token))
    return result

def _safe_parse(raw, on_rejected=None):
    try:
        return _conservative_parse(raw)
    except (ValueError, RecursionError) as first:
        # Capture even if the additional repair succeeds, so production
        # failures can become regression fixtures. Callback owns diagnostics.
        if on_rejected is not None:
            on_rejected(first)
        text = raw.strip()
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.IGNORECASE)
        if len(text) > MAX_REPAIR_CHARS or not text.startswith("{") or not text.endswith("}"):
            raise
        # A strict decode that rejected duplicate keys/nonfinite values
        # must not be bypassed by a tolerant parser collapsing those keys.
        try:
            _decode(text)
        except json.JSONDecodeError:
            pass
        except ValueError:
            raise first from None
        # Valid first object plus extra structured data is ambiguous, not
        # malformed syntax. Keep the previous rejection intact.
        try:
            _, end = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_constant).raw_decode(text)
        except json.JSONDecodeError:
            pass
        except ValueError:
            raise first from None
        else:
            if text[end:].strip():
                raise first from None
        # The legacy general extractor may attempt unicode_escape decoding
        # on an already-Unicode document. Prefer its narrowly scoped repair
        # primitives before any peeling or reserialization. In particular,
        # do not let a literal newline in Russian text damage every string.
        try:
            original_tokens = _extended_tokens(text)
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise first from None

        def checked_candidate(candidate):
            if original_tokens != _extended_tokens(candidate):
                raise ValueError("Backplane repair changed content tokens")
            value = _decode(candidate)
            if not isinstance(value, dict):
                raise ValueError("Invalid proposal")
            return value

        try:
            normalized = quote_unquoted_keys(escape_control_chars_inside_strings(text))
            value = checked_candidate(normalized)
        except (ValueError, TypeError, RecursionError, SyntaxError, OverflowError):
            pass
        else:
            log.warning("Model JSON recovered strategy=backplane_lexical_repair chars=%s", len(text))
            return value

        # Python's strict=False relaxes only literal control characters in
        # strings. It retains all data (including trailing whitespace that
        # the legacy helper may strip) and still rejects duplicate keys,
        # invalid structure and nonfinite constants via the same hooks.
        try:
            value = json.loads(text, strict=False, object_pairs_hook=_unique, parse_constant=_constant)
        except (ValueError, RecursionError):
            pass
        else:
            if isinstance(value, dict):
                log.warning("Model JSON recovered strategy=literal_control_chars chars=%s", len(text))
                return value

        try:
            value = checked_candidate(extract_json_new(text))
        except (ValueError, TypeError, RecursionError, SyntaxError, OverflowError) as repair_error:
            # Do not put model text or error.doc in logs.
            log.warning("Backplane JSON repair rejected reason=%s chars=%s",
                        type(repair_error).__name__, len(text))
            raise first from None
        log.warning("Model JSON recovered strategy=backplane_json_repair error=%s chars=%s",
                    type(first).__name__, len(text))
        return value

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class JsonResult:
    value: Any
    repaired: bool
    strategy: str

def parse_json(raw: str, *, policy: str = "safe", expected_type: type = dict,
               on_rejected=None, crash_logger: CrashLogger | None = None,
               recorder: CrashRecorder | None = None) -> JsonResult:
    """Parse JSON and optionally record terminal failures in a caller-owned folder.

    Crash recording is opt-in through recorder or crash_logger.
    Intermediate parse attempts that later recover are not recorded.
    """
    try:
        return _parse_json_impl(
            raw, policy=policy, expected_type=expected_type,
            on_rejected=on_rejected,
        )
    except Exception as error:
        if recorder is not None:
            _capture_failure(
                recorder,
                operation="parse_json",
                raw=raw,
                error=error,
                options={
                    "policy": policy,
                    "expected_type": getattr(expected_type, "__name__", type(expected_type).__name__),
                },
            )
        capture_failure(crash_logger, raw, format="json", policy=policy, error=error)
        raise


def _parse_json_impl(raw: str, *, policy: str = "safe", expected_type: type = dict,
                     on_rejected=None) -> JsonResult:
    """Strict first; SAFE checks admissible edits; TOLERANT uses legacy extraction.

    SAFE currently supports object documents only. It rejects ambiguous or
    incomplete input and does not promise semantic correctness of malformed
    documents. The callback receives a sanitized failure when strict decoding
    fails; it is optional and intended for caller-owned diagnostics.
    """
    if policy not in ("strict", "safe", "tolerant"):
        raise ValueError("Unknown JSON repair policy")
    if not isinstance(raw,str) or len(raw)>MAX_REPAIR_CHARS:
        raise ValueError("Invalid or oversized JSON input")
    if policy=="safe" and expected_type is not dict:
        raise ValueError("SAFE currently supports object documents only")
    try:
        value=_decode(raw)
    except json.JSONDecodeError as exc:
        if policy=="strict":
            raise
        if on_rejected is not None:
            on_rejected(ValueError(exc.msg))
        if policy=="safe":
            value=_safe_parse(raw)
        else:
            value=_decode(extract_json_new(raw))
        result=JsonResult(value,True,policy)
    else:
        result=JsonResult(value,False,"strict")
    if not isinstance(result.value,expected_type):
        raise ValueError("Unexpected JSON root type")
    return result
