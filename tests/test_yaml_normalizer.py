from llm_output_repair.yaml_normalizer import YamlNormalizer

def norm(s):  # helper
    return YamlNormalizer()._progressive_cleanup(s)

def test_multilayer_n_backslashes_outside_quotes():
    s = r"use_cases:\\n- id: UC-1\\n  title: T\\n"
    assert "\n" in norm(s)

def test_even_odd_backslashes_semantics():
    # нечётное -> \n
    assert norm(r"foo\\\nbar").count("\n") == 1  # (\\\n) -> (\ + \n)
    # чётное -> остаётся литерал \n
    t = norm(r"foo\\\\nbar")                      # (\\\\n) -> (\\n)
    assert r"\n" in t and "\n" not in t

def test_preserve_inside_yaml_quotes():
    s = r'- note: "don\'t touch \n here as literal \\n"\\n- next: ok\\n'
    out = norm(s)
    # внутри кавычек должно остаться "\n" как текст, а не перевод строки
    assert '"don\'t touch \\n here' in out
    assert "\n- next: ok\n" in out

def test_strip_codefence_and_wrappers():
    s = "```json\n\"use_cases:\\n- id: UC-1\\n\"\n```\n"
    out = norm(s)
    assert "use_cases:" in out and "\n- id: UC-1\n" in out
