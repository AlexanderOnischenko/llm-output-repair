import re
import ast
from typing import List, Optional, Callable


# === Module-level compiled regex patterns ===
_LINE_VALUE_RE = re.compile(
        r'^(?P<prefix>^\s*([a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)*):\s*(?=\S))'  # ключ и ':'
        r'(?P<val>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^#\n]+?)'  # значение
        r'(?P<comment>\s*#.*)?$'  # комментарий вне кавычек
    )
_LIST_VALUE_RE = re.compile(
    r'^(?P<prefix>\s*-\s*)'                                    # отступ + "- "
    r'(?P<val>'                                                 # захватываем значение, которое может быть:
      r'"(?:[^"\\]|\\.)*"|'                                    #   — двойная-кавычная строка
      r'\'(?:[^\'\\]|\\.)*\'|'                                 #   — одинарная-кавычная строка
      r'[^#\n]+'                                               #   — или любая последовательность не-# и не-"\n"
    r')'
    r'(?P<comment>\s*#.*)?$'                                    # комментарий (нежадно)
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

def is_mapping_line(line: str) -> bool:
    """
    Проверяет, является ли строка YAML-маппингом (key: value), а не сигнатурой функции.
    """
    in_single = in_double = in_parens = False
    for i, c in enumerate(line):
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == '(' and not in_single and not in_double:
            in_parens = True
        elif c == ')' and not in_single and not in_double:
            in_parens = False
        elif c == ':' and not in_single and not in_double and not in_parens:
            # Есть неэкранированный двоеточие вне кавычек и скобок
            # Убедимся, что перед ним есть ключ
            key = line[:i].strip()
            if key:  # Можно добавить доп.проверки: не начинается с `-`, `*`, `?`
                return True
    return False

def scalar_looks_like_special_yaml(val: str) -> bool:
    val = val.strip()
    if not val:
        return False
    if val[0] in {'*', '&', '!', '>', '|', '@'}:
        return True
    if re.match(r'^[\[\]\{\},:\?\-]+$', val):  # только спец. символы
        return True
    if re.match(r'^![\w\-]*$', val):  # yaml tag
        return True
    if re.match(r'^[>|][-+]?$', val):  # folded or literal
        return True
    return False

# === Inline mapping detection ===

def looks_like_inline_mapping(val: str) -> bool:
    stripped = val.strip()
    if len(stripped) < 2 or stripped[0] != '{' or stripped[-1] != '}':
        return False
    inner = stripped[1:-1]
    if not inner.strip():
        return True  # '{}'

    depth = 0
    in_single = False
    in_double = False
    escape = False
    colon_found = False

    for ch in inner:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if ch == '{':
            depth += 1
            continue
        if ch == '}':
            if depth == 0:
                return False
            depth -= 1
            continue
        if ch == ':' and depth == 0:
            colon_found = True
    return depth == 0 and not in_single and not in_double and colon_found

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


def _contains_unescaped_double_quote(text: str) -> bool:
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
def sanitize_escapes(lines: List[str]) -> List[str]:
    # Было (ломало `\\d` → `\\\d`):
    # return [re.sub(r'\\([^nt"\\])', r'\\\\\1', ln) for ln in lines]

    # Стало: удваиваем ТОЛЬКО одиночный backslash, не трогаем уже-удвоенные
    return [re.sub(r'(?<!\\)\\([^nt"\\])', r'\\\1', ln) for ln in lines]


def remove_unmatched(lines: List[str]) -> List[str]:
    """
    Remove unmatched brackets/braces/parentheses from each line individually,
    preserving quoted newlines by operating line-by-line.
    """
    cleaned_lines: List[str] = []
    for line in lines:
        # Track counts per line
        paren = brace = bracket = 0
        out_chars: List[str] = []
        for ch in line:
            if ch == '(':
                paren += 1; out_chars.append(ch)
            elif ch == ')' and paren > 0:
                paren -= 1; out_chars.append(ch)
            elif ch == '[':
                bracket += 1; out_chars.append(ch)
            elif ch == ']' and bracket > 0:
                bracket -= 1; out_chars.append(ch)
            elif ch == '{':
                brace += 1; out_chars.append(ch)
            elif ch == '}' and brace > 0:
                brace -= 1; out_chars.append(ch)
            elif ch not in ')]}':
                out_chars.append(ch)
        cleaned = ''.join(out_chars)
        # Fix unmatched quotes per line
        for quote in ('"', "'"):
            if cleaned.count(quote) % 2:
                idx = cleaned.rfind(quote)
                cleaned = cleaned[:idx] + cleaned[idx+1:]
        cleaned_lines.append(cleaned)
    return cleaned_lines


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
        new = []
        for it in items:
            if not it:
                continue
            if (re.fullmatch(r'".*"|\'.*\'', it)
                    or not scalar_needs_quotes(it)
                    or looks_like_inline_mapping(it)):
                new.append(it)
            else:
                new.append(quote_scalar(it))
        return '[' + ', '.join(new) + ']'
    return re.sub(r'\[([^\]]*)\]', repl, line)

def prequote_mapping(mapping: str) -> str:
    """
    Оборачивает ключ в mapping (key: value), если он требует кавычек.
    Например: **some text**: val  →  "**some text**": val
    """
    # Парсим ключ и значение
    m = re.match(r'^(?P<key>[^:]+):\s*(?P<val>.*)$', mapping)
    if not m:
        return mapping  # если не соответствует — возвращаем как есть

    key = m.group('key').strip()
    val = m.group('val').strip()

    # Если ключ уже в кавычках — оставляем
    if key and key[0] in ('"', "'") and key[-1] == key[0]:
        key_out = key
    elif scalar_needs_quotes(key) or scalar_looks_like_special_yaml(key):
        key_out = quote_scalar(key)
    else:
        key_out = key

    # НОВОЕ: аккуратно кавычим значение при необходимости
    if val and val[0] in ('"', "'") and val[-1] == val[0]:
        val_out = val
    elif (_NUMBER_RE.match(val) or _BOOL_RE.match(val)
          or _EMPTY_LIST_RE.match(val) or _ARRAY_RE.match(val)
          or looks_like_inline_mapping(val)):
        val_out = val
    elif scalar_needs_quotes(val) or scalar_looks_like_special_yaml(val):
        val_out = quote_scalar(val)
    else:
        val_out = val

    return f"{key_out}: {val_out}"

def prequote_line(line: str) -> str:
    # mapping key:value
    m = _LINE_VALUE_RE.match(line)
    if m:
        prefix, val, comment = m.group('prefix'), m.group('val').strip(), m.group('comment') or ''
        if (not val or _NUMBER_RE.match(val) or _BOOL_RE.match(val) or _NULL_RE.match(val)
            or _EMPTY_LIST_RE.match(val) or _ARRAY_RE.match(val)
            or looks_like_inline_mapping(val)):
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
        if is_mapping_line(val):
            return f"{prefix}{prequote_mapping(val)}{comment}"
        if (not val or _NUMBER_RE.match(val) or _BOOL_RE.match(val)
            or _EMPTY_LIST_RE.match(val) or _ARRAY_RE.match(val)
            or looks_like_inline_mapping(val)):
            return line
        if val[0] in ('"', "'") and val[-1] == val[0]:
            if val[0] == '"' and _contains_unescaped_double_quote(val[1:-1]):
                inner = val[1:-1]
                return f"{prefix}{quote_scalar(inner)}{comment}"
            return line
        if scalar_needs_quotes(val) | scalar_looks_like_special_yaml(val):
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

# === Default line processors for common fixes ===
def default_line_processor(line: str) -> str:
    """
    Fix trailing colon on type values: 'type: object:' -> 'type: object'
    """
    return re.sub(r'^(?P<i>\s*type:\s*)(?P<v>[\w-]+):(\s*)$', r"\g<i>\g<v>", line)

#  добавить рядом с прочими утилитами
_QUOTED_PAIR = re.compile(r'(?<!")\\\"([^"]*?)\\\"(?!")')  #  \"...\"  не внутри уже-двойных кавычек
_WRAPPED_SINGLE_DOUBLE = re.compile(r":\s*'\"(.*?)\"'\s*$") #   : '"..."'  → : "..."
_TRAILING_ARRAY_QUOTE = re.compile(r":\s*'\[\]\"'\s*$")   #   : '[]"'   → : []
_DOUBLE_QUOTED_ID = re.compile(r'^(\s*-\s+id:\s*)""([^"]+)"\s*$')  # - id: ""1"  → - id: "1"
_SINGLE_EMPTY_LIST = re.compile(r':\s*[\'"]\[\][\'"]\s*$')         # : '[]' или : "[]" → : []

def fix_llm_quotes(lines: List[str]) -> List[str]:
    out = []
    for ln in lines:
        # 1) починка ключа с лишней одинарной кавычкой и висящей открывающей "
        #    main_flow': "  →  main_flow:
        ln = re.sub(r"^(\s*)([A-Za-z0-9_-]+)'\s*:\s*\"?\s*$", r"\1\2:", ln)

        # 2)  \"...\"  →  "..."  (когда не внутри уже взятых в двойные кавычки скаляров)
        ln = _QUOTED_PAIR.sub(r'"\1"', ln)

        # 3)  : '"..."' → : "..."
        ln = _WRAPPED_SINGLE_DOUBLE.sub(r': "\1"', ln)

        # 4)  : '[]"' → : []
        ln = _TRAILING_ARRAY_QUOTE.sub(r": []", ln)

        # 🔧 НОВОЕ: починить - id: ""1"
        m = _DOUBLE_QUOTED_ID.match(ln)
        if m:
            ln = f'{m.group(1)}"{m.group(2)}"'

        # 🔧 НОВОЕ: : '[]' или : "[]" → : []
        if _SINGLE_EMPTY_LIST.search(ln):
            ln = _SINGLE_EMPTY_LIST.sub(": []", ln)

        # 5) одиночные случаи \" и \' в «плоском» значении (встречается в id: \"1\")
        #    безопасно для YAML, т.к. потом prequote_line обработает кавычки корректно
        ln = ln.replace(r"\'", "'").replace(r"\\\"", r"\"")  # сначала схлопнуть \\"
        ln = ln.replace(r"\"", "\"")  # и уже потом \" → "
        out.append(ln)
    return out

def _split_yaml_line(line: str):
    s = line
    n = len(s)
    in_s = in_d = False
    esc = False
    # найдём первую "структурную" двоеточие для mapping-строки вне кавычек
    colon_pos = -1
    i = 0
    while i < n:
        ch = s[i]
        if esc:
            esc = False
            i += 1
            continue
        if ch == '\\':
            esc = True
            i += 1
            continue
        if ch == "'" and not in_d:
            in_s = not in_s
            i += 1
            continue
        if ch == '"' and not in_s:
            in_d = not in_d
            i += 1
            continue
        if ch == ':' and not in_s and not in_d:
            colon_pos = i
            break
        i += 1

    # Комментарий ищем от начала value (или всей строки), вне кавычек
    def _split_comment(from_idx: int):
        in_s2 = in_d2 = False
        esc2 = False
        j = from_idx
        while j < n:
            ch2 = s[j]
            if esc2:
                esc2 = False
                j += 1
                continue
            if ch2 == '\\':
                esc2 = True
                j += 1
                continue
            if ch2 == "'" and not in_d2:
                in_s2 = not in_s2
                j += 1
                continue
            if ch2 == '"' and not in_s2:
                in_d2 = not in_d2
                j += 1
                continue
            if ch2 == '#' and not in_s2 and not in_d2:
                return s[from_idx:j].rstrip(), s[j:]
            j += 1
        return s[from_idx:].rstrip(), ""

    line_stripped = line.rstrip("\n")
    if colon_pos != -1:
        # mapping: PREFIX = до ':' + пробелы после, VALUE = до #, COMMENT = с #
        after = colon_pos + 1
        while after < n and s[after] == ' ':
            after += 1
        val, comment = _split_comment(after)
        prefix = s[:after]
        return prefix, val, comment
    else:
        # возможно это list-item "- value"
        m = re.match(r'^(\s*-\s+)(.*)$', line_stripped)
        if m:
            prefix = m.group(1)
            rest = m.group(2)
            val, comment = _split_comment(len(prefix))
            # _split_comment возвращал от исходного индекса, поправим:
            val = rest if comment == "" else rest[:len(val)].rstrip()
            comment = "" if comment == "" else line_stripped[len(prefix) + len(val):]
            return prefix, val, comment
        # ни mapping, ни list-item — возвращаем как есть
        return "", line_stripped, ""

def _has_unescaped_quote(text: str, q: str) -> bool:
    i = 0
    n = len(text)
    while i < n:
        if text[i] == q:
            # подсчёт backslash перед кавычкой
            b = 0
            j = i - 1
            while j >= 0 and text[j] == '\\':
                b += 1
                j -= 1
            if b % 2 == 0:  # неэкранированная
                return True
        i += 1
    return False

def _escape_unescaped_double_quotes(text: str) -> str:
    """Заменяет только НЕэкранированные `"` на `\"` и нормализует избыточные слэши перед `"` до одного."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            # считаем слэши слева
            b = 0
            j = i - 1
            while j >= 0 and text[j] == '\\':
                b += 1
                j -= 1
            if b == 0:
                out.append('\\"')  # экранируем
            else:
                # уже экранировано, но слэшей могло быть много: оставим один
                out = out[:len(out) - b]  # убрать уже добавленные b слэшей
                out.append('\\\"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)

def _yaml_single_quote(text: str) -> str:
    """ЯМЛ-правило: внутри одинарно-кавычечной строки кавычка удваивается."""
    # Сделаем идемпотентно: превращаем любые группы ' в чётные
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "'":
            # посчитаем подряд идущие '
            j = i
            while j < n and text[j] == "'":
                j += 1
            length = j - i
            if length % 2 == 1:
                # нечётная — добавим ещё одну
                out.append("'" * (length + 1))
            else:
                out.append("'" * length)
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)

def _smart_quote_scalar(val: str) -> str:
    """Выбирает безопасные кавычки для скаляра и приводит внутренние кавычки в порядок."""
    v = val.strip()
    if v == "" or v in ("[]", "{}",):
        return v

    # flow-списки не трогаем — этим занимается _requote_flow_sequences
    if "[" in v and v.find("[") < (v.find("#") if "#" in v else 10**9):
        return v

    # уже в двойных кавычках
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        inner = v[1:-1]
        # если внутри есть неэкранированные двойные кавычки
        if _has_unescaped_quote(inner, '"'):
            # если нет одинарных — выгоднее перейти на одинарные кавычки YAML
            if "'" not in inner:
                return "'" + _yaml_single_quote(inner) + "'"
            # иначе остаёмся на двойных и экранируем только неэкранированные "
            fixed = _escape_unescaped_double_quotes(inner)
            return '"' + fixed + '"'
        # нормализуем избыточные слэши перед кавычками, оставляя `\"`
        fixed = _escape_unescaped_double_quotes(inner)
        return '"' + fixed + '"'

    # уже в одинарных кавычках
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        inner = v[1:-1]
        # YAML в одинарных кавычках не понимает backslash-эскейпы, просто удваиваем '
        return "'" + _yaml_single_quote(inner) + "'"

    # без кавычек — подберём тип
    needs_quote = bool(re.search(r'\s|"|\'|:|#', v))
    if not needs_quote:
        return v

    # предпочтём одинарные, если нет одинарных внутри
    if "'" not in v:
        return "'" + _yaml_single_quote(v) + "'"
    # иначе двойные + экранируем только неэкранированные "
    return '"' + _escape_unescaped_double_quotes(v) + '"'

def fix_nested_quotes(lines):
    """Перекавычивает проблемные значения (map/list) так, чтобы YAML оставался валиден,
    стараясь держать разные типы кавычек снаружи/внутри; для двойных — оставляет один слэш у внутренних.
    """
    out = []
    for ln in lines:
        prefix, val, comment = _split_yaml_line(ln)
        if prefix:  # mapping или list-item
            new_val = _smart_quote_scalar(val)
            out.append(f"{prefix}{new_val}{comment}")
        else:
            out.append(ln)
    return out

_DQ_SPAN_SPLIT = re.compile(r'("(?:[^"\\]|\\.)*")')  # сегменты: вне/внутри двойных кавычек

def _decode_transport_outside_quotes(seg: str) -> str:
    """
    ВНЕ кавычек:
      - k=1,2,3 слэша перед n/r/t -> реальный перевод строки (с оставшимся (k-1)//2 слэшами слева)
      - k=4,6,...  -> оставляем литерал (не превращаем в реальный \n)
    Ровно то, что ожидают тесты 1 и 2.
    """
    def repl(m: re.Match) -> str:
        k = len(m.group(1))
        ch = m.group(2)
        if k % 2 == 0:
            if k == 2:  # \\n -> \n (реальный)
                return {'n': '\n', 'r': '\r', 't': '\t'}[ch]
            else:
                return m.group(0)  # чётные >=4 – не трогаем
        else:
            # 1,3,5,... -> снимаем один уровень до реального перевода
            return ('\\' * ((k - 1) // 2)) + {'n': '\n', 'r': '\r', 't': '\t'}[ch]
    return re.sub(r'(\\+)([nrt])', repl, seg)

def _collapse_inside_yaml_quotes(seg: str) -> str:
    """
    ВНУТРИ кавычек:
      - не превращаем \n/\t/\r в реальные переводы
      - схлопываем пары слэшей перед n/r/t: \\n -> \n (литерал), \\\n -> \\n и т.п.
      - убираем ОДИНОЧНОЕ экранирование апострофа: don\'t -> don't
        (но оставляем \\': это реальный слэш + апостроф)
    """
    # 1) пары слэшей перед n/r/t (как было)
    def repl_nrt(m: re.Match) -> str:
        k = len(m.group(1))
        ch = m.group(2)
        return ('\\' * ((k + 1) // 2)) + ch
    seg = re.sub(r'(\\+)([nrt])', repl_nrt, seg)

    # 2) снять одиночное экранирование апострофа внутри двойных кавычек
    #    ...don\'t... -> ...don't...
    seg = re.sub(r'(?<!\\)\\\'', "'", seg)

    return seg


def _decode_once_context_aware(text: str) -> str:
    """
    Один контекстный проход распаковки транспорта.
    """
    parts = _DQ_SPAN_SPLIT.split(text)
    for i in range(len(parts)):
        if i % 2 == 0:  # вне кавычек
            parts[i] = _decode_transport_outside_quotes(parts[i])
        else:           # внутри кавычек
            parts[i] = _collapse_inside_yaml_quotes(parts[i])
    return ''.join(parts)

def _strip_transport_wrappers(text: str) -> str:
    t = text  # БЕЗ .strip() — не убиваем значимые \n

    # 0) убрать префиксы типа `text=` / `content=`
    m = re.match(r'^(?:[a-zA-Z_]+)\s*=\s*(.*)$', t)
    if m:
        t = m.group(1)

    # 1) markdown-кодфенс (начало)
    t = re.sub(r'^```[a-zA-Z0-9_-]*\n', '', t)
    #    markdown-кодфенс (конец)
    t = re.sub(r'\n```(?:\s*)$', '', t)

    # 2) снять ВНЕШНИЕ кавычки целиком, если блок ими обёрнут (без зачистки \n)
    if (t.startswith("'") and t.endswith("'")) or (t.startswith('"') and t.endswith('"')):
        t = t[1:-1]

    # 3) если это строго «строковый литерал» python/json — попробуем один раз распаковать
    tj = t
    if (tj.startswith('"') and tj.endswith('"')) or (tj.startswith("'") and tj.endswith("'")):
        try:
            t = ast.literal_eval(tj)  # снимет ЭТОТ слой \" и \\n → \n
        except Exception:
            pass

    return t


# === Normalizer classes ===
class YamlNormalizer:
    def __init__(self, line_processors: Optional[List[Callable[[str], str]]] = None):
        self.line_processors = line_processors or []

    _CODEFENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n|\n```$")

    def _strip_transport_wrappers(self, text: str) -> str:
        t = text.strip()

        # 0) убрать тривиальные префиксы/суффиксы логов вида: text='....'
        # оставляем только правую часть после первым '=', если это явно 'text=' или 'content='
        m = re.match(r'^(?:[a-zA-Z_]+)\s*=\s*(.*)$', t)
        if m:
            t = m.group(1).strip()

        # 1) убрать markdown-кодфенс (начало/конец)
        # делаем два прохода: сначала верх, потом низ
        if t.startswith("```"):
            # срежем первую строку с ```... и финальные ```
            t = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", t)
        if t.endswith("```"):
            t = re.sub(r"\n```$", "", t)

        # 2) снять *внешние* кавычки, если весь блок ими обёрнут
        if (t.startswith("'") and t.endswith("'")) or (t.startswith('"') and t.endswith('"')):
            t = t[1:-1]

        # 3) иногда внутрь попадает JSON-строка (с \" и \\n).
        # попробуем аккуратно распаковать ЕЁ, если это строго один JSON-строковый литерал.
        tj = t.strip()
        if (tj.startswith('"') and tj.endswith('"')) or (tj.startswith("'") and tj.endswith("'")):
            # ast.literal_eval устойчивее к смешанным экранированиям python repr
            try:
                t = ast.literal_eval(tj)
            except Exception:
                pass

        return t

    _DQ_SPAN_SPLIT = re.compile(r'("(?:[^"\\]|\\.)*")')  # разбивка по YAML-двойным кавычкам

    @staticmethod
    def _collapse_escapes_segment(seg: str) -> str:
        """
        Снимаем один слой экранирования \n \r \t для ЛЮБОГО числа слэшей перед n|r|t.
        Работает только на данном сегменте (вне кавычек).
        """
        def _one_pass(s: str) -> str:
            # k слэшей перед (n|r|t)
            def repl(m):
                slashes = m.group(1)
                ch = m.group(2)  # n|r|t
                k = len(slashes)
                if k % 2 == 1:
                    # нечётное: это экранирование -> k//2 слэшей + реальный символ
                    decoded = {'n': '\n', 'r': '\r', 't': '\t'}[ch]
                    return ('\\' * (k // 2)) + decoded
                else:
                    # чётное: это литерал backslash + буква -> уменьшаем стек слэшей вдвое и оставляем \ch
                    return ('\\' * (k // 2)) + '\\' + ch
            return re.sub(r'(\\+)([nrt])', repl, s)

        prev = seg
        # повторяем, пока меняется (снимаем слой за слоем)
        for _ in range(6):  # ограничитель, чтобы точно не зациклиться
            nxt = _one_pass(prev)
            if nxt == prev:
                break
            prev = nxt
        return prev

    def _decode_transport_newlines_safely(self, text: str) -> str:
        """
        Итеративно снимаем экранирование \n/\r/\t ВНЕ YAML-двойных кавычек.
        """
        parts = self._DQ_SPAN_SPLIT.split(text)
        # части на чётных индексах — вне кавычек; нечётные — сами кавычки (оставляем как есть)
        for i in range(0, len(parts), 2):
            parts[i] = self._collapse_escapes_segment(parts[i])
        return "".join(parts)

    def _progressive_cleanup(self, t: str) -> str:
            t = _strip_transport_wrappers(t)   # как у вас, но БЕЗ .strip() на концах
            t = _decode_once_context_aware(t)          # новый контекстный проход
            return t

    def normalize(self, raw_text: str) -> str:
        # raw_text = self._progressive_cleanup(raw_text)
        # 1) Split into lines respecting quoted newlines
        lines = split_yaml_lines(raw_text)
        # 2) Structural cleanup
        for fn in [decode_escapes, normalize_continuations, sanitize_escapes, remove_unmatched]:
            lines = fn(lines)
        lines = fix_llm_quotes(lines)
        # lines = fix_nested_quotes(lines)
        # 3) Per-line fixes
        procs = self.line_processors or [default_line_processor, quote_arrays, prequote_line]
        for proc in procs:
            lines = [proc(ln) for ln in lines]
        # 4) Repair indentation last
        lines = repair_indentation(lines)
        return "\n".join(lines)