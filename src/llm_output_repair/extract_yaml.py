import re
import ast
import yaml
import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from io import StringIO
from dataclasses import is_dataclass, asdict
from typing import List, Any, Mapping, Iterable, Optional
try:
    from pydantic import BaseModel
except ImportError:  # Optional: only needed to serialize Pydantic models in dump_yaml.
    BaseModel = None
import logging
logger = logging.getLogger(__name__)

# === Module-level compiled regex patterns ===
_LINE_VALUE_RE = re.compile(
        r'^(?P<prefix>^\s*([a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)*):\s*(?=\S))'  # ключ и ':'
        r'(?P<val>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^#\n]+?)'  # значение
        r'(?P<comment>\s*#.*)?$'  # комментарий вне кавычек
    )
# _LIST_VALUE_RE = re.compile(
#     r'^(?P<prefix>\s*-\s*)'
#         r'(?P<val>"(.+?))'  # значение
#         r'(?P<comment>\s*#.*)?$'  # комментарий вне кавычек
# )
_LIST_VALUE_RE = re.compile(
    r'^(?P<prefix>\s*-\s*)'
    r'(?P<val>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^#\n]+?)'
    r'(?P<comment>\s*#.*)?$'
)

_NEEDS_QUOTE_RE = re.compile(r"[\s:\[\]\{\},'" + r"\\]|^(?:true|false|null|[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)$",
                              re.IGNORECASE)
_NUMBER_RE = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')
_BOOL_RE = re.compile(r'^(?:true|false)$', re.IGNORECASE)
_NULL_RE = re.compile(r'^(?:null|~)$', re.IGNORECASE)
_EMPTY_LIST_RE = re.compile(r'^\[\]$')
_ARRAY_RE = re.compile(r'^\[\s*[^,\]]+(?:\s*,\s*[^,\]]+)*\s*\]$')
_LIST_PATTERN = re.compile(r'^- ')                       # '- ' элемент
_MAPPING_PATTERN = re.compile(r'^[^\\-\\*][^:]+:')    # mapping


# === Common utilities ===

def split_yaml_lines(text: str) -> List[str]:
    """
    Split on real '\n' but ignore newlines inside double quotes.
    """
    lines: List[str] = []
    current: List[str] = []
    in_quote = False
    escape = False
    for ch in text:
        if ch == '"' and not escape:
            in_quote = not in_quote
        if ch == '\\' and not escape:
            escape = True
            current.append(ch)
            continue
        if ch == '\n' and not in_quote:
            lines.append(''.join(current))
            current = []
            escape = False
            continue
        current.append(ch)
        escape = False
    lines.append(''.join(current))
    return lines

# === Quoting logic ===

def scalar_needs_quotes(s: str) -> bool:
    if re.fullmatch(r'^[\w\./-]+$', s) and s.lower() not in ('true', 'false', 'null'):
        return False
    return bool(_NEEDS_QUOTE_RE.search(s))

def scalar_quote_style(s: str) -> Optional[str]:
    """
    Return '"' or "'" if needed, else None.
    """
    if not scalar_needs_quotes(s):
        return None
    if "'" in s:
        return '"'
    return "'"

def quote_scalar(s: str) -> str:
    style = scalar_quote_style(s)
    if style == '"':
        esc = s.replace('"', '\\"')
    else:
        esc = s.replace("'", "''")
    return f"{style}{esc}{style}"

# Custom YAML dumper to apply quoting
class QuotedDumper(yaml.SafeDumper):
    # запрещаем indentless-режим
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, indentless=False)
    def ignore_aliases(self, data):
        return True


def _repr_str(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    style = scalar_quote_style(data)
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)

QuotedDumper.add_representer(str, _repr_str)

# === Processing pipeline on list of lines ===

def decode_escapes(lines: List[str]) -> List[str]:
    escapes = {"\\n": "\n", "\\t": "\t", '\\"': '"', "\\'": "'"}
    out: List[str] = []
    for line in lines:
        parts = re.split(r'("(?:[^"\\]|\\.)*")', line)
        for i in range(0, len(parts), 2):
            seg = parts[i]
            for esc, ch in escapes.items():
                seg = seg.replace(esc, ch)
            parts[i] = seg
        out.append(''.join(parts))
    return out


def normalize_continuations(lines: List[str]) -> List[str]:
    """
    Сливает строки-«продолжения», которые
    начинаются с отступа и не содержат ':' к предыдущей строке.
    Например:
      goal: Безопасное объединение
        старой и новых версий
    станет:
      goal: Безопасное объединениес старой и новых версий
    """
    result: List[str] = []
    block_pipe_indent = None   
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        # 1) Обнаружили начало block scalar (pipe literal indicator)
        if block_pipe_indent is None and stripped.rstrip().endswith('|'):
            block_pipe_indent = indent
            result.append(line)
            continue
        # 2) Если внутри block scalar — сливаем сюда все переносы
        if block_pipe_indent is not None and indent > block_pipe_indent:
            # склеиваем с пред. строкой через пробел
            result[-1] += ' ' + stripped
            continue
        # 3) Вышли из блока — сбрасываем режим
        if block_pipe_indent is not None and indent <= block_pipe_indent:
            block_pipe_indent = None
        is_list_item = stripped.startswith('- ')
        is_mapping  = bool(re.match(r"^\s*[^:\s].*?:", line))
        if (indent > 0 
            and block_pipe_indent is None
            and not is_list_item 
            and not is_mapping):
            # сливаем с предыдущей строкой
            if result:
                result[-1] += ' ' + stripped
            else:
                result.append(line)
        else:
            result.append(line)
    return result

# def sanitize_escapes(lines: List[str]) -> List[str]:
#     return [re.sub(r'\\(?![nt"\\])', r'\\\\', ln) for ln in lines]

# def sanitize_escapes(lines: List[str]) -> List[str]:
#     # Было: r'\\(?![nt"\\])' — ловило и конец строки
#     # Стало: удваиваем \ только если за ним СУЩЕСТВУЕТ символ ≠ n,t,",\
#     return [re.sub(r'\\([^nt"\\])', r'\\\\\1', ln) for ln in lines]

def sanitize_escapes(lines: List[str]) -> List[str]:
    # Было (ломало `\\d` → `\\\d`):
    # return [re.sub(r'\\([^nt"\\])', r'\\\\\1', ln) for ln in lines]

    # Стало: удваиваем ТОЛЬКО одиночный backslash, не трогаем уже-удвоенные
    return [re.sub(r'(?<!\\)\\([^nt"\\])', r'\\\1', ln) for ln in lines]


def remove_unmatched(lines: List[str]) -> List[str]:
    text = '\n'.join(lines)
    paren = bracket = brace = 0
    res_chars: List[str] = []
    for ch in text:
        if ch == '(':
            paren += 1; res_chars.append(ch)
        elif ch == ')' and paren > 0:
            paren -= 1; res_chars.append(ch)
        elif ch == '[':
            bracket += 1; res_chars.append(ch)
        elif ch == ']' and bracket > 0:
            bracket -= 1; res_chars.append(ch)
        elif ch == '{':
            brace += 1; res_chars.append(ch)
        elif ch == '}' and brace > 0:
            brace -= 1; res_chars.append(ch)
        else:
            if ch not in ')]}':
                res_chars.append(ch)
    cleaned = ''.join(res_chars)
    for q in ('"', "'"):
        if cleaned.count(q) % 2:
            idx = cleaned.rfind(q)
            cleaned = cleaned[:idx] + cleaned[idx+1:]
    return cleaned.split('\n')


def _split_array_items(content: str) -> List[str]:
    items: List[str] = []
    buf: List[str] = []
    in_s = in_d = False
    escape = False
    depth = 0
    for ch in content:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            continue
        if ch == '"' and not in_s:
            in_d = not in_d
            buf.append(ch)
            continue
        if ch == "'" and not in_d:
            in_s = not in_s
            buf.append(ch)
            continue
        if ch in "[{" and not in_s and not in_d:
            depth += 1
            buf.append(ch)
            continue
        if ch in "]}" and not in_s and not in_d and depth > 0:
            depth -= 1
            buf.append(ch)
            continue
        if ch == ',' and not in_s and not in_d and depth == 0:
            items.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    items.append(''.join(buf).strip())
    return items


def quote_arrays(line: str) -> str:
    def repl(m: re.Match) -> str:
        items = _split_array_items(m.group(1))
        new = [it if (re.fullmatch(r'".*"|\'.*\'', it) or not scalar_needs_quotes(it)) else quote_scalar(it)
               for it in items if it]
        return '[' + ', '.join(new) + ']'
    return re.sub(r'\[([^\]]*)\]', repl, line)


def _contains_unescaped_double_quote(text: str) -> bool:
    """Возвращает True, если внутри строки встречается НЕэкранированная двойная кавычка."""
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            return True
    return False


def prequote_line(line: str) -> str:
    # mapping key:value
    m = _LINE_VALUE_RE.match(line)
    if m:
        prefix, val, comment = m.group('prefix'), m.group('val').strip(), m.group('comment') or ''
        if (not val or _NUMBER_RE.match(val) or _BOOL_RE.match(val) or _NULL_RE.match(val)
            or _EMPTY_LIST_RE.match(val) or _ARRAY_RE.match(val)):
            return line
        if val[0] in ('"', "'") and val[-1] == val[0]:
            if val[0] == '"' and _contains_unescaped_double_quote(val[1:-1]):
                inner = val[1:-1]
                return f"{prefix}{quote_scalar(inner)}{comment}"
            return line
        if scalar_needs_quotes(val):
            return f"{prefix}{quote_scalar(val)}{comment}"
        return line
    # list element
    lm = _LIST_VALUE_RE.match(line)
    if lm:
        prefix, val, comment = lm.group('prefix'), lm.group('val').strip(), lm.group('comment') or ''
        # если это на самом деле вложенный mapping (foo: bar) — пропускаем
        if re.match(r'^[^"\']+?:', val):
            return line
        if (not val or _NUMBER_RE.match(val) or _BOOL_RE.match(val)
            or _EMPTY_LIST_RE.match(val) or _ARRAY_RE.match(val)):
            return line
        if val[0] in ('"', "'") and val[-1] == val[0]:
            if val[0] == '"' and _contains_unescaped_double_quote(val[1:-1]):
                inner = val[1:-1]
                return f"{prefix}{quote_scalar(inner)}{comment}"
            return line
        if scalar_needs_quotes(val):
            return f"{prefix}{quote_scalar(val)}{comment}"
        return line
    return line

# === Indentation repair works per line list ===

def repair_indentation(lines: List[str]) -> List[str]:
    out: List[str] = []
    key_stack: List[int] = []          # уровни отступов ключей "foo:"
    list_indent_stack: List[int] = []  # отступы текущих элементов списка
    for line in lines:
        stripped = line.lstrip()
        indent   = len(line) - len(stripped)
        # 0) Если у нас уже были ключи, и на нулевом уровне
        #    встретим строку, не ключ и не list-item — это трейлер, выходим.
        if key_stack and indent == key_stack[0] \
           and not stripped.startswith("- ") \
           and not _MAPPING_PATTERN.match(stripped):
            if not stripped:
                continue
            elif not stripped.endswith("\n"):
                out.append("")
            break

        # «поднимаемся» — сбрасываем стеки
        while list_indent_stack and indent < list_indent_stack[-1]:
            list_indent_stack.pop()
        while key_stack and indent < key_stack[-1]:
            key_stack.pop()

        # элемент списка
        if _LIST_PATTERN.match(stripped):
            if key_stack:                      # список внутри карты
                target = key_stack[-1] + 2
                line = " " * target + stripped
                indent = target
            # запоминаем, какой отступ имеет этот элемент
            if not list_indent_stack or indent != list_indent_stack[-1]:
                list_indent_stack.append(indent)
            out.append(line)
            continue

        # строка-ключ карты
        if _MAPPING_PATTERN.match(stripped):
            # если ключ оказался на том же уровне, что и дефис списка → сдвигаем
            if list_indent_stack and indent == list_indent_stack[-1]:
                indent = list_indent_stack[-1] + 2
                line = " " * indent + stripped
            key_stack.append(indent)
            out.append(line)
            continue

        # остальные строки без изменений
        out.append(line)
    return out

_MAP_OPEN_QUOTED_CONTAINER_RE = re.compile(r'^(?P<prefix>\s*[^#\n]+?:\s*)"(?!")(?=\s*[\[\{])(?P<tail>.*)$')
_LIST_OPEN_QUOTED_CONTAINER_RE = re.compile(r'^(?P<prefix>\s*-\s*)"(?!")(?=\s*[\[\{])(?P<tail>.*)$')

def _fix_multiline_quoted_containers(text: str) -> str:
    lines = text.splitlines()
    changed = False
    for i, ln in enumerate(lines):
        m = _MAP_OPEN_QUOTED_CONTAINER_RE.match(ln) or _LIST_OPEN_QUOTED_CONTAINER_RE.match(ln)
        if m:
            # просто убираем первую кавычку перед [ или {
            lines[i] = f"{m.group('prefix')}{m.group('tail')}"
            changed = True
    out = "\n".join(lines)
    if changed:
        # если осталась висячая закрывающая кавычка в самом конце — срежем
        out = re.sub(r'"\s*\Z', "", out)
    return out

# === Main beautification ===

def beautify_yaml_from_string(raw: str) -> Any:
    raw = _fix_multiline_quoted_containers(raw)
    lines = split_yaml_lines(raw)
    lines = decode_escapes(lines)
    lines = _unbox_quoted_containers(lines)
    lines = normalize_continuations(lines)
    lines = sanitize_escapes(lines)
    lines = remove_unmatched(lines)
    lines = repair_indentation(lines)
    # array quoting and prequoting operate per-line
    lines = [quote_arrays(ln) for ln in lines]
    lines = [prequote_line(ln) for ln in lines]
    text = "\n".join(lines)
    try:
        return yaml.safe_load(StringIO(text))
    except Exception as e:
        raise ValueError(f"YAML parse error: {e}\nCleaned YAML:\n{text}")

# === API functions ===

def strip_comments(text: str) -> str:
    lines = text.splitlines()
    body_started = False
    body: List[str] = []
    for ln in lines:
        if not body_started:
            if ln.lstrip().startswith('#'):
                continue
            body_started = True
        if body_started:
            body.append(ln)
    return '\n'.join(body)

_CODE_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*|\s*```\s*$", re.MULTILINE)
_RAWLLM_REPR_RE = re.compile(
    r"RawLLMOutput\(\s*text=(?P<q>['\"])(?P<body>.*?)(?P=q)\s*(?:,|\))",
    re.DOTALL
)

def _strip_code_fences(s: str) -> str:
    return _CODE_FENCE_RE.sub("", s)

def _unwrap_rawllm_repr(s: str) -> str:
    m = _RAWLLM_REPR_RE.search(s)
    return m.group("body") if m else s

def _maybe_unquote_entire_string(s: str) -> str:
    """
    Если вся строка в кавычках — аккуратно разворачиваем literal.
    Поддерживает как одинарные, так и двойные кавычки.
    """
    st = s.strip()
    if len(st) >= 2 and st[0] in ("'", '"') and st[-1] == st[0]:
        try:
            return ast.literal_eval(st)  # безопасно для строковых литералов
        except Exception:
            # fallback: самые частые эскейпы
            return (st[1:-1]
                    .replace(r"\n", "\n")
                    .replace(r"\t", "\t")
                    .replace(r"\\", "\\")
                    .replace(r"\'", "'")
                    .replace(r"\"", '"'))
    return s

def _strip_trailing_garbage(s: str) -> str:
    """
    Убираем типичные хвосты после YAML (закрывающая } без {, остатки ``` и т.п.)
    Только очень консервативно.
    """
    out = s.rstrip()

    # Убираем завершающие "голые" null/None — частый артефакт усечённого JSON,
    # когда после последнего элемента LLM дописывает `null` без ключа.
    lines = out.splitlines()
    trimmed = False
    while lines and lines[-1].strip() in {"null", "None"}:
        lines.pop()
        trimmed = True
    if trimmed:
        out = "\n".join(lines).rstrip()

    # После удаления лишнего null может остаться хвостовой запятая перед закрывающей
    # скобкой/фигурной скобкой. Это делает документ невалидным как JSON/YAML, поэтому
    # мягко убираем её.
    out = re.sub(r'([\]\}])\s*,\s*(?=\s*[\]\}]|\s*$)', r'\1', out)

    # Если на конце одинокая '}' без '{' — похоже, прилетела от JSON-ish обёртки
    if out.endswith("}") and "{" not in out:
        out = out[:-1].rstrip()

    # Снять завершающую ``` если осталась
    if out.endswith("```"):
        out = out[:out.rfind("```")].rstrip()

    return out

_QUOTED_CONTAINER_MAP_RE = re.compile(
    r'^(?P<prefix>\s*[^#\n]+?:\s*)(?P<q>["\'])(?P<body>\s*[\[\{].*[\]\}])(?P=q)\s*(?P<comment>\s*#.*)?$'
)
_QUOTED_CONTAINER_LIST_RE = re.compile(
    r'^(?P<prefix>\s*-\s*)(?P<q>["\'])(?P<body>\s*[\[\{].*[\]\}])(?P=q)\s*(?P<comment>\s*#.*)?$'
)

def _unbox_quoted_containers(lines: List[str]) -> List[str]:
    def unbox(line: str) -> str:
        m = _QUOTED_CONTAINER_MAP_RE.match(line) or _QUOTED_CONTAINER_LIST_RE.match(line)
        if not m:
            return line
        body = m.group("body")
        # аккуратно снимаем только кавычечные эскейпы внутри контейнера
        body = (body
                .replace(r"\\r\\n", "\n")
                .replace(r"\\n", "\n")
                .replace(r"\\t", "\t")
                .replace(r"\\\"", '"')
                .replace(r"\\'", "'"))
        return f"{m.group('prefix')}{body}{m.group('comment') or ''}"
    return [unbox(ln) for ln in lines]

# === NEW: нормализация объектов в YAML-дружественные примитивы ===

_PRIMITIVE_SCALARS = (str, int, float, bool, type(None))

def _asdict_dc(o: Any) -> dict:
    return asdict(o)  # type: ignore[arg-type]

def _to_yamlable(obj: Any) -> Any:
    # Примитивы — как есть
    if isinstance(obj, _PRIMITIVE_SCALARS):
        return obj

    # Частые «скалярные» типы → строкой
    if isinstance(obj, (UUID, datetime, date, Decimal)):
        return str(obj)

    # Enum → значение (или имя)
    if isinstance(obj, enum.Enum):
        return obj.value if isinstance(obj.value, _PRIMITIVE_SCALARS) else str(obj.value)

    # Pydantic BaseModel → dict
    if BaseModel is not None and isinstance(obj, BaseModel):
        # pydantic v2: model_dump; v1: .dict
        dump_fn = getattr(obj, "model_dump", None) or getattr(obj, "dict", None)
        if dump_fn:
            try:
                data = dump_fn(exclude_none=True)
            except TypeError:
                data = dump_fn()
            return _to_yamlable(data)

    # dataclass → dict
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_yamlable(_asdict_dc(obj)) 

    # Mapping → рекурсивно
    if isinstance(obj, Mapping):
        return { _to_yamlable(k): _to_yamlable(v) for k, v in obj.items() }

    # Iterable (list/tuple/set/…)
    if isinstance(obj, Iterable) and not isinstance(obj, (str, bytes, bytearray)):
        return [ _to_yamlable(x) for x in obj ]

    # Фолбэк: строковое представление
    return str(obj)


def dump_yaml(data: Any) -> str:
    """
    Безопасный дамп в YAML:
    - конвертирует Pydantic модели, dataclass’ы и нетривиальные типы в примитивы
    - сохраняет вашу кастомную кавычную стратегию
    """
    yamlable = _to_yamlable(data)
    return yaml.dump(
        yamlable,
        Dumper=QuotedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        indent=2,
    )

def extract_yaml_new(raw: str) -> Any:
    # code-block extraction
    match = re.search(r'```yaml(.*?)```', raw, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return extract_yaml_new(match.group(1).strip())
    stripped = raw.strip()

    s = stripped

    # 0) Снять fenced code, если было
    s = _strip_code_fences(s)

    # 1) Развернуть RawLLMOutput(..., text='...')
    s = _unwrap_rawllm_repr(s)

    # 2) Если вся строка целиком в кавычках — разворачиваем
    s = _maybe_unquote_entire_string(s)

    # 3) Частые экранированные последовательности без внешних кавычек
    # 3) Нормализуем конкретные эскейпы (БЕЗ глобального '\\' -> '\')
    s = (s.replace("\\r\\n", "\n")
           .replace("\\n", "\n")
           .replace("\\t", "\t")
           .replace('\\"', '"')
           .replace("\\'", "'"))

    # 3a) Убираем «слэш + реальный перевод строки» (склейка LLM)
    s = re.sub(r'\\\r?\n', '\n', s)

    # 3b) Снимаем висячие backslash'и в КОНЦЕ строк
    s = re.sub(r'(?m)\\$', '', s)

    # 4) Снять типичные хвосты
    stripped = _strip_trailing_garbage(s)

    # 4a) НЕ снимаем кавычки здесь повторно — _maybe_unquote_entire_string уже сделал, если надо
    stripped = strip_comments(stripped)
    if not stripped:
        return None

    data = beautify_yaml_from_string(stripped)
    if isinstance(data, dict) and None in data:
        val = data[None]
        if val in (None, {}) or (isinstance(val, list) and not val):
            data.pop(None)
    return data


def extract_yaml(raw: str) -> str:
    result = extract_yaml_new(raw)
    if result is None:
        return raw
    return dump_yaml(result)

# что это, зачем это???
def extract_yaml_no_beautify(raw: str) -> str | None:
        # code-block extraction
    match = re.search(r'```yaml(.*?)```', raw, flags=re.DOTALL | re.IGNORECASE)
    if match:
        # мы тут вообще соответствие типов смотрим?
        return dump_yaml(extract_yaml_new(match.group(1).strip()))
    stripped = raw.strip()
    if stripped and stripped[0] in ('"', "'") and stripped[0] == stripped[-1]:
        stripped = stripped[1:-1]
    stripped = strip_comments(stripped)
    if not stripped:
        return None
    return stripped
