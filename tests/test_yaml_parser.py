import pytest
import yaml

from llm_output_repair.extract_yaml import (
    repair_indentation,
    normalize_continuations,
    quote_arrays,
    extract_yaml_new
)

def _to_lines(s: str):
    # новый код ждёт список строк
    return s.splitlines()

def _join_lines(lines):
    # удобная склейка + выравнивание финального переноса
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


@pytest.mark.parametrize("inp,exp", [
    # 1) Простой случай: ключ на нулевом отступе, список без отступа → вложить + выкинуть трейлер
    (
        "components:\n- name: A\n- name: B\nFooter",
        "components:\n  - name: A\n  - name: B\n"
    ),
    # 2) Различный регистр и комментарий
    (
        "Components:  # uppercase C\n- foo\n- bar\nTrailer",
        "Components:  # uppercase C\n  - foo\n  - bar\n"
    ),
    # 3) Если уже есть правильный отступ — не трогаем
    (
        "key:\n  - x:1\n  - x:2",
        "key:\n  - x:1\n  - x:2\n"
    ),
    # 4) Root-level список остаётся
    (
        "- a\n- b\n",
        "- a\n- b\n"
    ),
    # 5) Несколько уровней ключей: работает для каждого уровня
    (
        "root:\nchild:\n- c1\n- c2\nother: value",
        "root:\nchild:\n  - c1\n  - c2\nother: value\n"
    ),
])
def test_repair_indentation(inp, exp):
    got_lines = repair_indentation(_to_lines(inp))
    got = _join_lines(got_lines)
    assert got == exp
    # и проверим, что safe_load теперь отрабатывает без ошибок
    yaml.safe_load(got)


@pytest.mark.parametrize("raw, expected", [
    # 1) Элемент списка без смещения под ключом → вглубь на +2
    (
        "root:\n"
        "- name: item1\n"
        "  key: value\n",
        "root:\n"
        "  - name: item1\n"
        "    key: value\n"
    ),

    # 2) Ключ ровно на уровне дефиса (нужно +2)
    (
        "list:\n"
        "  - foo: bar\n"
        "  baz:\n",
        "list:\n"
        "  - foo: bar\n"
        "    baz:\n"
    ),

    # 3) Корректный YAML не меняется
    (
        "integration_view:\n"
        "  - use_case: UC\n"
        "    modifies: []\n",
        "integration_view:\n"
        "  - use_case: UC\n"
        "    modifies: []\n"
    ),

    # 4) Смешанный inline-список и блочный список
    (
        "seq:\n"
        "  - a: 1\n"
        "  - b: 2\n"
        "map:\n"
        "  x: y\n",
        "seq:\n"
        "  - a: 1\n"
        "  - b: 2\n"
        "map:\n"
        "  x: y\n"
    ),
])
def test_repair_yaml_indentation_2(raw, expected):
    repaired = _join_lines(repair_indentation(_to_lines(raw)))
    assert repaired == expected
    yaml.safe_load(repaired)


# Тест нормализации продолжений
def test_normalize_continuations():
    raw = 'goal: Безопасное объединение старой и новых версий\n  через LLM'
    out_lines = normalize_continuations(_to_lines(raw))
    out_text = "\n".join(out_lines)
    assert 'goal: Безопасное объединение старой и новых версий через LLM' in out_text
    yaml.safe_load(out_text)


def test_quote_arrays():
    inp = 'tags: [agents, feature/{name}, simple]'
    out = quote_arrays(inp)
    # Проверяем смысл, а не стиль кавычек
    assert yaml.safe_load(out) == {"tags": ["agents", "feature/{name}", "simple"]}

def test_quote_mapping_value_with_colon_inside_list():
    raw = (
        "use_cases:\n"
        "  - id: UC-2\n"
        "    main_flow:\n"
        "      - Step 2: Установка structural.layer: TBD\n"
    )
    from llm_output_repair.yaml_normalizer import YamlNormalizer
    out = YamlNormalizer().normalize(raw)
    # критично: обе части кавычены
    assert "- 'Step 2': 'Установка structural.layer: TBD'" in out
    import yaml
    yaml.safe_load(out)  # не должно падать

def test_extract_yaml_new_unescapes_escaped_newlines_and_quotes():
    raw = "\"use_cases:\\n  - id: UC-1\\n    title: \\\"T\\\"\\n\""
    data = extract_yaml_new(raw)
    assert isinstance(data, dict)
    assert data["use_cases"][0]["title"] == "T"

def test_extract_yaml_new_removes_backslash_before_real_newline():
    # Важно: в конце первой строки стоит ЛИТОВЫЙ backslash + реальный \n
    raw = "use_cases:\n  - id: UC-1\\\n    title: T\n"
    data = extract_yaml_new(raw)
    assert data["use_cases"][0]["id"] == "UC-1"
    assert data["use_cases"][0]["title"] == "T"

def test_extract_yaml_new_handles_rawllm_repr_wrapper():
    raw = (
        "RawLLMOutput(text='use_cases:\\n"
        "  - id: UC-1\\n"
        "    title: T', finish_reason=None)"
    )
    data = extract_yaml_new(raw)
    assert data["use_cases"][0]["title"] == "T"

@pytest.mark.parametrize("garbage_tail", ["}\n```", "\n```\n", "\n</code>"])
def test_extract_yaml_new_strips_common_trailing_garbage(garbage_tail):
    raw = f"use_cases:\n  - id: UC-1\n    title: T\n{garbage_tail}"
    data = extract_yaml_new(raw)
    assert data["use_cases"][0]["id"] == "UC-1"