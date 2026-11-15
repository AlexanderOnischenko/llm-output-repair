"""Transport and root-key normalization for generated YAML."""
import json
import re
from typing import List, Tuple

def _rootkey_targeted_clean(s: str, root_key: str) -> str:
    text = s

    # 1) вырезать префикс вида `":   <root_key>:"`
    preamble_re = re.compile(rf'^\s*["\']?\s*:\s*["\']?\s*(?={re.escape(root_key)}\s*:)', re.MULTILINE)
    text = preamble_re.sub("", text)

    # 2) если ключ встречается — отрезать всё до него (оставить YAML с корня)
    idx = text.find(f"{root_key}:")
    if idx != -1:
        text = text[idx:]

    # 3) мягко подрезать типовые хвосты после блока
    for marker in ("\n```", "\n}\n", "\n}\r", "\"}", "'}", "\n</code>"):
        p = text.rfind(marker)
        if p != -1:
            tail = text[p + len(marker):]
            if not tail.strip():
                text = text[:p]
                break

    return text.strip()


def _requote_flow_sequences(s: str) -> str:
    """
    Чинит НЕвложенные flow-списки [ ... ]:
      - корректно разбивает элементы по запятым (вне кавычек);
      - снимает лишние внешние кавычки;
      - лечит хвостовые "" и \\" в конце элемента;
      - расщепляет склеенные строки внутри одного токена (…\\",\\\"… / …", "…).
    """
    SPLIT_RE = re.compile(r'"\s*,\s*"')  # граница между "...", "..."

    def _count_unescaped_double_quotes(text: str) -> int:
        cnt = 0
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '"':
                # считаем количество подряд идущих backslash перед кавычкой
                j = i - 1
                b = 0
                while j >= 0 and text[j] == '\\':
                    b += 1
                    j -= 1
                if b % 2 == 0:  # неэкранированная кавычка
                    cnt += 1
            i += 1
        return cnt

    def _rstrip_unescaped_double_quotes(text: str) -> str:
        """Снимает подряд идущие НЕэкранированные `"` с хвоста."""
        i = len(text) - 1
        stop = i
        while i >= 0 and text[i] == '"':
            # проверяем, экранирована ли эта кавычка
            j = i - 1
            b = 0
            while j >= 0 and text[j] == '\\':
                b += 1
                j -= 1
            if b % 2 == 0:   # неэкранированная — снимаем
                stop = i - 1
                i -= 1
                continue
            break
        return text[:stop+1]

    def _fix_tail_quotes(text: str) -> str:
        """
        Лечим «мусорные» хвосты кавычек у токена:
          - удаляем завершающие \\" (экранированные) и "" (неэкранированные) серии на конце;
          - если число НЕэкранированных кавычек остаётся нечётным — доснимаем хвост.
        """
        # 1) убираем хвостовые экранированные кавычки типа ...\"
        while text.endswith('\\"'):
            text = text[:-2]

        # 2) убираем хвостовые НЕэкранированные кавычки: ..."", ...""" и т.п.
        text = _rstrip_unescaped_double_quotes(text)

        # 3) если после этого число НЕэкранированных кавычек внутри токена нечётное,
        #    попробуем ещё раз снять хвостовую серию (бывают смешанные случаи)
        if _count_unescaped_double_quotes(text) % 2 == 1:
            text = _rstrip_unescaped_double_quotes(text)

        return text

    def _strip_balanced_quotes(tok: str) -> str:
        if len(tok) >= 2 and ((tok[0] == tok[-1] == '"') or (tok[0] == tok[-1] == "'")):
            return tok[1:-1]
        return tok

    def _retokenize(content: str) -> List[str]:
        items, buf = [], []
        in_s = in_d = esc = False
        for ch in content:
            if esc:
                buf.append(ch); esc = False; continue
            if ch == '\\':
                buf.append(ch); esc = True; continue
            if ch == "'" and not in_d:
                in_s = not in_s; buf.append(ch); continue
            if ch == '"' and not in_s:
                in_d = not in_d; buf.append(ch); continue
            if ch == ',' and not in_s and not in_d:
                items.append("".join(buf).strip()); buf = []; continue
            buf.append(ch)
        items.append("".join(buf).strip())
        return items

    def _split_stuck_items(core: str) -> List[str]:
        # нормализуем слой экранирования (нужно только для поиска границы)
        tmp = core.replace('\\\\"', '\\"').replace('\\"', '"')
        if SPLIT_RE.search(tmp):
            parts = SPLIT_RE.split(tmp)
            if parts and parts[0].startswith('"'):
                parts[0] = parts[0][1:]
            if parts and parts[-1].endswith('"'):
                parts[-1] = parts[-1][:-1]
            return parts
        return [core]

    def _process_token(t: str) -> List[str]:
        core = _strip_balanced_quotes(t).strip()
        chunks = _split_stuck_items(core)
        out: List[str] = []
        for ch in chunks:
            ch = _fix_tail_quotes(ch)  # ← ключевой шаг: снимаем мусорные хвостовые кавычки
            out.append(json.dumps(ch, ensure_ascii=False))
        return out

    def _fix_match(m: re.Match) -> str:
        inner = m.group(1)
        # Если внутри есть многострочные структуры или YAML-объекты, не трогаем —
        # это уже нормальный блок, и превращать его в строку нельзя.
        if any(ch in inner for ch in "{}\n"):
            return m.group(0)
        if not inner.strip():
            return "[]"
        toks = _retokenize(inner)
        rebuilt: List[str] = []
        for t in toks:
            rebuilt.extend(_process_token(t))
        return "[" + ", ".join(rebuilt) + "]"

    # Ограничение: только одноуровневые []
    return re.sub(r"\[([^\[\]]+)\]", _fix_match, s)

def _transport_preprocess_yaml_text(raw: str) -> Tuple[str, dict]:
    """
    Превращает «грязную» строку (экранированные \\n, \\", JSON-обёртка {"text": ...})
    в обычный YAML-текст. Возвращает (prepared_text, meta).
    """
    meta = {
        "took_from_json_text_field": False,
        "unescaped_backslash_escapes": False,
        "flow_lists_single_item_quoted": 0,
        "flow_lists_requoted": False,
        "outer_quotes_removed": False,
        "dropped_trailing_brace": False,
    }
    s = (raw or "").strip()

    # 1) Иногда прилетает JSON-обёртка или JSON-строковый литерал
    if s.startswith("{") or s.startswith("["):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                s = obj["text"]
                meta["took_from_json_text_field"] = True
            elif isinstance(obj, str):
                s = obj
                meta["took_from_json_text_field"] = True
        except json.JSONDecodeError:
            pass

    # 2) Разэкранировать \n, \t, \", \uXXXX и т.п., если видим литералы
    if "\\n" in s or "\\t" in s or '\\"' in s or "\\u" in s:
        try:
            # Самый безопасный способ: отдать JSON-декодеру как строковый литерал
            wrapped = '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
            s = json.loads(wrapped)
            meta["unescaped_backslash_escapes"] = True
        except Exception:
            s = (s.replace("\\n", "\n")
                   .replace("\\t", "\t")
                   .replace('\\"', '"')
                   .replace("\\\\", "\\"))
            meta["unescaped_backslash_escapes"] = True

    stripped_for_wrap = s.strip()
    if stripped_for_wrap.startswith(("'", '"')) and len(stripped_for_wrap) > 1:
        quote = stripped_for_wrap[0]
        closing_idx = stripped_for_wrap.rfind(quote)
        if closing_idx > 0:
            suffix = stripped_for_wrap[closing_idx + 1 :]
            suffix_stripped = suffix.strip()
            suffix_clean = suffix_stripped.replace("\\n", "").replace("\\r", "")
            if suffix_clean in ("", "}"):
                s = stripped_for_wrap[1:closing_idx]
                if suffix_clean == "}":
                    meta["dropped_trailing_brace"] = True
                meta["outer_quotes_removed"] = True

    # 3) Нормализуем переводы строк
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    # 4) Чиним flow-списки с одним элементом, содержащим пробелы:
    #    [Feature Refiner Agent] -> ["Feature Refiner Agent"]
    def _fix_flow_single_item(m: re.Match) -> str:
        inner = m.group(1).strip()
        # не трогаем, если несколько элементов (запятая), пусто или уже в кавычках
        if "," in inner or not inner:
            return m.group(0)
        if (inner.startswith('"') and inner.endswith('"')) or (inner.startswith("'") and inner.endswith("'")):
            return m.group(0)
        # Не трогаем структуры { ... } / [ ... ] или многострочные блоки — это уже YAML,
        # а не «слово с пробелом», и превращать их в строки опасно.
        if any(ch in inner for ch in "{}[]:\n"):
            return m.group(0)
        if " " in inner:
            meta["flow_lists_single_item_quoted"] += 1
            return "[" + json.dumps(inner, ensure_ascii=False) + "]"
        return m.group(0)

    s = re.sub(r"\[([^\[\]]+)\]", _fix_flow_single_item, s)

    s2 = _requote_flow_sequences(s)
    if s2 != s:
        meta["flow_lists_requoted"] = True
        s = s2

    return s, meta
    
