import json
import re
import pytest

from llm_output_repair.extract_json import (
    extract_json_new,
    extract_bracketed_json,
    strip_comments,
    remove_think_sections,
    _fix_stray_backslashes_outside_strings,
    _has_esc_outside_strings,
    _replace_ellipsis_outside_strings,
    _prune_ellipsis_markers,
    _escape_control_chars_inside_strings,
)

# ─────────────────────────────────────────────────────────────
# 0) САНИТИ-КЕЙСЫ: «чистый» JSON без мусора
# ─────────────────────────────────────────────────────────────

def test_pass_through_clean_object():
    src = '{"a":1,"b":[2,3]}'
    out = extract_json_new(src)
    assert json.loads(out) == {"a": 1, "b": [2, 3]}

def test_pass_through_clean_array():
    src = '[{"x":"y"}]'
    out = extract_json_new(src)
    assert json.loads(out) == [{"x": "y"}]

# ─────────────────────────────────────────────────────────────
# 1) ВЫДЕЛЕНИЕ ПЕРВОГО СБАЛАНСИРОВАННОГО БЛОКА (extract_bracketed_json)
# ─────────────────────────────────────────────────────────────

def test_extract_block_simple_object():
    txt = '{"a": 1, "b": [2, 3]}'
    assert extract_bracketed_json(txt) == txt

def test_extract_block_simple_array():
    arr = '[1, {"k": "value"}, 3]'
    assert extract_bracketed_json(f"XYZ {arr} ABC") == arr

def test_extract_block_prefers_first_between_array_and_object():
    txt = ' [1,2] {"a":1}'
    assert extract_bracketed_json(txt) == '[1,2]'

def test_extract_block_ignores_outside_braces_and_picks_valid():
    txt = 'x}y{z {"a":1} w'
    assert extract_bracketed_json(txt) == '{"a":1}'

def test_extract_block_multiple_pick_first_json_span():
    txt = 'foo {"a":1} bar [2,3]'
    assert extract_bracketed_json(txt) == '{"a":1}'

def test_extract_block_bom_and_ws_prefix():
    txt = '\ufeff   \n\t{"k":1}'
    assert extract_bracketed_json(txt) == '{"k":1}'

def test_extract_block_nested_arrays_and_objects():
    txt = 'pre [1, {"a":2}] post'
    assert extract_bracketed_json(txt) == '[1, {"a":2}]'

def test_extract_block_ignores_brackets_inside_strings():
    txt = '{"text": "{ not a brace }", "num": 5}'
    assert extract_bracketed_json(txt) == txt

def test_extract_block_escaped_quotes_and_backslashes_inside_string():
    txt = '{"t":"she said: \\"{ not brace }\\"","path":"C:\\\\tmp\\\\f.json"}'
    assert extract_bracketed_json(txt) == txt

def test_extract_block_unicode_and_emojis_in_string():
    txt = '{"msg": "привет 👋"}'
    assert extract_bracketed_json(txt) == txt

def test_extract_block_unbalanced_returns_none():
    txt = '{"incomplete": [1, 2"'
    assert extract_bracketed_json(txt) is None

def test_extract_block_ignores_unmatched_outer_and_captures_inner():
    txt = 'noise { junk {"a":1} trailing-without-close'
    assert extract_bracketed_json(txt) == '{"a":1}'

def test_extract_block_json_in_text_middle():
    prefix = 'Intro '
    obj = '{"x": {"y": 2}}'
    suffix = ' End'
    txt = prefix + obj + suffix
    assert extract_bracketed_json(txt) == obj

# ─────────────────────────────────────────────────────────────
# 2) КОММЕНТАРИИ И МЯГКИЕ ПРАВКИ СНАРУЖИ (strip_comments, beautify)
# ─────────────────────────────────────────────────────────────

def test_line_comments_removed_but_urls_kept():
    raw = r'''
    {
      "url": "https://api.example.com", // trailing comment
      // full line comment
      "k": 1
    }
    '''
    data = json.loads(extract_json_new(raw))
    assert data["url"] == "https://api.example.com"
    assert data["k"] == 1

def test_block_comments_removed():
    raw = r'''
    {
      /* block
         comment */
      "x": 2
    }
    '''
    data = json.loads(extract_json_new(raw))
    assert data["x"] == 2

def test_strip_comments_only_outside_strings():
    text = '"https://a/b" // cmt\n{"a":1}'
    cleaned = strip_comments(text)
    assert 'https://a/b' in cleaned
    assert '{"a":1}' in cleaned

def test_single_quoted_strings_and_unquoted_keys_fixed():
    raw = "{a: 'b', c: 1,}"
    out = json.loads(extract_json_new(raw))
    assert out == {"a": "b", "c": 1}

def test_missing_commas_between_adjacent_strings_fixed():
    raw = '["a"\n"b"\n"c"]'
    out = json.loads(extract_json_new(raw))
    assert out == ["a","b","c"]

def test_bare_identifier_in_array_is_quoted_and_preserved():
    raw = """
    {
      "diagram": [
        "line1",
        "line2"
        end
      ]
    }
    """
    out = json.loads(extract_json_new(raw))
    assert out == {"diagram": ["line1", "line2", "end"]}

def test_unmatched_outer_brackets_stripped():
    raw = '[[ {"a":1} ]]]}'
    out = json.loads(extract_json_new(raw))
    assert out == [[{"a":1}]]

def test_invalid_then_beautified_success():
    raw = '{a:1,}'  # невалидный JSON, beautify чинит
    assert '"a": 1' in extract_json_new(raw)

# ─────────────────────────────────────────────────────────────
# 3) FENCES И THINK-СЕКЦИИ
# ─────────────────────────────────────────────────────────────

def test_remove_think_sections_outside_fences_only():
    text = 'A <think>secret</think> B\n```json\n{"x":1}\n```\nC </think> D'
    cleaned = remove_think_sections(text)
    assert '<think>' not in cleaned
    assert '</think>' not in cleaned
    assert '{"x":1}' in cleaned

def test_extract_from_json_fence_wins():
    text = """blah
    ```json
    { "a": 1, "b": 2 }
    ```
    tail"""
    out = extract_json_new(text)
    assert json.loads(out) == {"a": 1, "b": 2}

def test_outer_noise_clamped_by_brackets_even_with_noise():
    raw = 'header }}} {"x": {"y": 2}} tail {{{'
    out = json.loads(extract_json_new(raw))
    assert out["x"]["y"] == 2

#─────────────────────────────────────────────────────────────
#4) ЛИШНИЕ БЭКСЛЭШИ СНАРУЖИ (ядро: _fix_stray_backslashes_outside_strings, _has_esc_outside_strings)
#─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("src,expected_block", [
    (r'\" : {\"a\":1}', '": {"a":1}'),
    (r'\{ "k": 1 \}', '{ "k": 1 }'),
    (r'\[ 1, 2, 3 \]', '[ 1, 2, 3 ]'),
    (r'{ \"k\": 1 }', '{ "k": 1 }'),
])
def test_fix_stray_backslashes_outside_strings_unit(src, expected_block):
    fixed = _fix_stray_backslashes_outside_strings(src)
    if expected_block.startswith('":'):
        assert fixed.startswith('":') or fixed.startswith('" :')
    assert re.sub(r'\s+', '', fixed) == re.sub(r'\s+', '', expected_block)

def test_has_esc_outside_strings_detects_structurals_threshold():
    s = r'\{ "a": 1 \}'
    assert _has_esc_outside_strings(s) is True
    s2 = '{ "a": "\n" }' # только внутри строки — не считается
    assert _has_esc_outside_strings(s2) is False

def test_end_to_end_stray_backslashes_enable_block_detection():
    src = r'noise noise \" : {\n  \"a\": 1\n}\n tail'
    out = extract_json_new(src)
    assert json.loads(out) == {"": {"a": 1}}

#─────────────────────────────────────────────────────────────
#5) ЭЛЛИПСИСЫ
#─────────────────────────────────────────────────────────────

def test_ellipsis_replaced_outside_strings_then_pruned():
    txt = '{ "arr": [1, 2, ..., 4], "x": "literal ... inside string" }'
    replaced = _replace_ellipsis_outside_strings(txt)
    assert '"ELLIPSIS"' in replaced
    obj = json.loads(replaced)
    pruned = _prune_ellipsis_markers(obj)
    assert pruned == {"arr": [1, 2, 4], "x": "literal ... inside string"}

def test_end_to_end_ellipsis_cleaned():
    src = '{ "a": 1, "b": [ "x", ..., "y" ] }'
    out = extract_json_new(src)
    assert json.loads(out) == {"a": 1, "b": ["x", "y"]}

#─────────────────────────────────────────────────────────────
#6) УПРАВЛЯЮЩИЕ СИМВОЛЫ ВНУТРИ СТРОК
#─────────────────────────────────────────────────────────────

def test_escape_control_chars_inside_strings_preserves_newline_and_strips_trailing_ws():
    raw = '"line1\n \t "' # \n + хвост пробелов/табов после него
    fixed_inner = _escape_control_chars_inside_strings(raw)
    assert fixed_inner.endswith('"')
    content = json.loads(fixed_inner) if fixed_inner.startswith('"') else fixed_inner
    assert content.endswith('\n') and not content.endswith('\n ')

def test_extract_json_sanitizes_control_chars_in_deep_structure():
    raw = r'''{
    "status": "success",
    "matched_adrs": [{
    "adr_id": "example-id",
    "relevance": 3,
    "rationale": "Если нужно ускорить поиск… в большом объеме.
    "
    }]
    }'''
    out = extract_json_new(raw)
    data = json.loads(out)
    assert data["matched_adrs"][0]["rationale"].endswith("\n")

def test_inner_single_quotes_not_broken():
    raw = '''
    {
    "traceability_file": [
    "Requirement 'logical_view' is mapped to:"
    ]
    }
    '''
    out = extract_json_new(raw)
    data = json.loads(out)
    assert data["traceability_file"][0] == "Requirement 'logical_view' is mapped to:"

#─────────────────────────────────────────────────────────────
#7) СЛОИ unicode_escape / «JSON в строке»
#─────────────────────────────────────────────────────────────

def test_unicode_escape_is_ok():
    raw = '{"msg":"\u041f\u0440\u0438\u0432\u0435\u0442"}'
    out = json.loads(extract_json_new(raw))
    assert out["msg"] == "Привет"

@pytest.mark.parametrize("src,expected", [
    (r'"{"a":1}"', {"a": 1}),       # json-в-строке (один слой)
    (r'"{\"a\":1}"', {"a": 1}),     # два слоя экранирования (слэш перед кавычками)
    (r'"\n{"a":1}\n"', {"a": 1}),   # переносы строк вокруг
])
def test_unicode_escape_layers(src, expected):
    out = extract_json_new(src)
    assert json.loads(out) == expected

def test_single_escape_quotes_inside_strings_survive():
    raw = r'''{
      "diagram_file": [
        "G[2.2] -- sends prompt \"Merge the following two versions...\" to LLM"
      ],
      "traceability_file": []
    }'''
    out = extract_json_new(raw)
    data = json.loads(out)
    assert data["diagram_file"][0] == (
        'G[2.2] -- sends prompt "Merge the following two versions..." to LLM'
    )

def test_log_string_with_literal_newlines_unwraps_array():
    raw = ''''{\n "diagram_file": [\n "flowchart TD",\n " ext_user[Пользователь] -->|Спецификация фичи| example_step[2.1: Инициализация Example View]",\n " example_components: ✔ | Конфликты версий компонентов |",\n "rules:",\n "- Для ext_llm: legacy → использовать fallback-валидацию"\n ]\n}\n}' '''
    out = extract_json_new(raw)
    obj = json.loads(out)
    assert isinstance(obj["diagram_file"], list)
    assert obj["diagram_file"][0] == "flowchart TD"

def test_two_layer_stringified_with_fence_unwrap():
    raw = r''''": {\n "diagram_file": [\n "flowchart TD",\n " subgraph Inputs",\n ...\n ]\n }\n}\n```"}' '''
    out = extract_json_new(raw)
    data = json.loads(out)
    assert "diagram_file" in data
    assert data["diagram_file"][0] == "flowchart TD"

def test_multilayer_string_array_unwrap():
    raw = r''' '[\n    \\"graph LR\\\\n\\",\n    \\"subgraph System Context\\\\n\\",\n    \\"end\\\\n\\" ]' '''
    out = extract_json_new(raw)
    data = json.loads(out)
    assert isinstance(data, list)
    # в зависимости от пути преобразований может остаться \n или реальный перевод строки
    assert data[0] == "graph LR\n" or data[0] == "graph LR\\n"

#─────────────────────────────────────────────────────────────
#8) ИДЕМПОТЕНТНОСТЬ
#─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("src", [
'{"a":1,"b":[2,3]}',
'{ "k": "{"nested":1}" }',
'{ "a": 1, "b": ["x", "y"] }',
])
def test_idempotency(src):
    out1 = extract_json_new(src)
    out2 = extract_json_new(out1)
    assert json.loads(out1) == json.loads(out2)

#─────────────────────────────────────────────────────────────
#9) БЕЗОПАСНОСТЬ ПРАВОК: не трогаем внутри строк
#─────────────────────────────────────────────────────────────

def test_do_not_touch_brackets_inside_strings():
    src = '{"s":"[not real] {json}"}'
    out = extract_json_new(src)
    assert json.loads(out) == {"s": "[not real] {json}"}

def test_do_not_insert_commas_inside_strings():
    # внутри объекта идёт "line1" "line2" подряд — 
    # запятая добавляется СНАРУЖИ, а вторая строка потом выбрасывается
    src = '{"s":"line1"\n"line2"}'
    out = extract_json_new(src)
    obj = json.loads(out)
    assert obj["s"] == "line1"

#─────────────────────────────────────────────────────────────
#10) СЛОЖНЫЕ END-TO-END КЕЙСЫ
#─────────────────────────────────────────────────────────────

def test_conversation_case_unbalanced_and_fences_and_ellipsis():
    src = r"""
    ": {\n "diagram_file": [\n
    "flowchart TD",\n
    " subgraph Inputs",\n
    ...\n
    ]\n }\n}\n```"}'
    """
    out = extract_json_new(src)
    data = json.loads(out)
    assert "" in data
    assert "diagram_file" in data[""]
    assert data[""]["diagram_file"][0] == "flowchart TD"
    assert data[""]["diagram_file"][1].strip().startswith("subgraph")

def test_drop_unmatched_outer_brackets_safely():
    src = '}} { "a": 1 } ]'
    out = extract_json_new(src)
    assert json.loads(out) == {"a": 1}

def test_fence_inside_string_is_ignored():
    raw = r'''
    {
      "diagram_file": [
        "graph LR",
        "```mermaid",
        "A-->B",
        "```"
      ],
      "status": "ok"
    }
    '''
    out = extract_json_new(raw)
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["diagram_file"][1] == "```mermaid"

def test_skip_stray_label_block_before_valid_json():
    raw = r"""{
  "diagram_file": [
    "flowchart TD",
    "  ExampleAgent[ExampleAgent
</think>

{
  "diagram_file": [
    "flowchart TD",
    "  subgraph Example_Context [Example Context]",
    "    direction TB"
  ],
  "traceability_file": [
    "| UC1 | 2.1 | ✔ |"
  ]
}
"""
    out = extract_json_new(raw)
    data = json.loads(out)
    assert data["diagram_file"][0] == "flowchart TD"
    assert any("Example Context" in row for row in data["diagram_file"])
    assert data["traceability_file"][0].startswith("| UC1")