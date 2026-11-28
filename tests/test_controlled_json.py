import unittest
from llm_output_repair import parse_json

def parse_proposal_json(raw, on_rejected=None):
    return parse_json(raw, on_rejected=on_rejected).value

class ProposalJsonTests(unittest.TestCase):
    def test_clean_and_trailing_prose(self):
        expected = {"text": 'цитата } [ " \\', "n": 1, "items": [1, 2]}
        import json
        raw = json.dumps(expected, ensure_ascii=False)
        self.assertEqual(parse_proposal_json(raw), expected)
        self.assertEqual(parse_proposal_json(raw + "\nГотово."), expected)

    def test_safe_decodes_a_single_labeled_json_fence(self):
        import json

        expected = {"text": 'строка ``` "a" [', "nested": [{"value": 3}]}
        body = json.dumps(expected, ensure_ascii=False)
        for heading, end_of_line in (("Response data", "\n"), ("Ответ модели", "\r\n")):
            raw = end_of_line.join((f"{heading}:", "  ```JSON ", body, "  ``` "))
            with self.subTest(heading=heading):
                result = parse_json(raw)
                self.assertEqual(result.value, expected)
                self.assertTrue(result.repaired)
                self.assertEqual(result.strategy, "safe")
                with self.assertRaises(json.JSONDecodeError):
                    parse_json(raw, policy="strict")

    def test_safe_rejects_labeled_fences_without_one_strict_object(self):
        fence = '```json\n{"value": 1}\n```'
        for raw in (
            f"Response data:\n{fence}\n{fence}",
            f"Response data:\n{fence}\nTruncated output",
            f'{{"value": 2}}\nResponse data:\n{fence}',
            f"true:\n{fence}",
            'Response data:\n{"value": 1}',
            'Response data:\n```json\n{"value": 1,}\n```',
            'Response data:\n```json\n{"value": 1, "value": 2}\n```',
            'Response data:\n```json\n{"value": NaN}\n```',
            'Response data:\n```json\n{"value": 1e400}\n```',
            'Response data:\n```json\n[{"value": 1}]\n```',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError) as raised:
                parse_proposal_json(raw)
            self.assertIs(type(raised.exception), ValueError)

    def test_comma_repair(self):
        self.assertEqual(parse_proposal_json('{"a":1,"b":[2,3,],}'), {"a":1,"b":[2,3]})
        self.assertEqual(parse_proposal_json('{,"a":1}'), {"a": 1})
        self.assertEqual(parse_proposal_json('{"a":1 "b":2}'), {"a":1,"b":2})
        self.assertEqual(
            parse_proposal_json('{"a":[{"b":"текст,]"} {"c":[true false,]},],}'),
            {"a": [{"b": "текст,]"}, {"c": [True, False]}]},
        )

    def test_reject_ambiguous_or_incomplete(self):
        for raw in ('{"a":1} {"a":2}', '{"a":1} trailing {"b":2}',
                    '{"a":1} [2]', '{"a":1} true', '{"a":1} 2',
                    '{"a":1', '{"a":}', '{"a":1,"a":2}',
                    '{"a":1,"a":2,}', '{"a":NaN}', '{"a":[01]}', '[]', 'not JSON'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_proposal_json(raw)


    def test_backplane_extended_repair_and_capture(self):
        import json
        raw = '{"a":"line1\nline2","b":2}'
        seen = []
        self.assertEqual(parse_proposal_json(raw, on_rejected=seen.append),
                         {"a":"line1\nline2","b":2})
        self.assertEqual(len(seen), 1)
        self.assertEqual(parse_proposal_json('{a:1,}'), {"a":1})
        self.assertEqual(parse_proposal_json('{"a":1}'), {"a":1})

    def test_backplane_does_not_salvage_by_dropping_data(self):
        for raw in ('{"a":"line1"\n"line2"}', '{"a":[1,...,2]}',
                    '{"a":"{\"nested\":1}"}', '{"a":1,"a":2,}',
                    '{"a":1} {"a":2}', '{"a":1,"b":2'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_proposal_json(raw)


    def test_cyrillic_nested_control_chars_preserve_entire_document(self):
        import json
        expected = {
            "artifact": {"role": [{"title": "Бухгалтер", "sources": [{"quote": "Нужен бухгалтер"}]}]},
            "assistant_message": "Первая\nВторая",
            "path": "C:\\tmp\\файл",
            "literal": r"\u041f",
            "n": 1e-7,
            "quote": 'Она сказала: "да".',
        }
        raw = json.dumps(expected, ensure_ascii=False)
        damaged = raw.replace(r"Первая\nВторая", "Первая\nВторая")
        self.assertNotEqual(damaged, raw)
        self.assertEqual(parse_proposal_json(damaged), expected)
        # Exercise the same library's lexical key repair on a whole document.
        damaged = damaged.replace('"artifact":', 'artifact:', 1)
        self.assertEqual(parse_proposal_json(damaged), expected)

    def test_control_repair_preserves_trailing_whitespace_and_rejects_duplicates(self):
        self.assertEqual(parse_proposal_json('{"text":"Привет\n  "}'), {"text":"Привет\n  "})
        with self.assertRaises(ValueError):
            parse_proposal_json('{"text":"Привет\n","text":"другое"}')


    def test_surplus_delimiters_and_unicode(self):
        import json
        from llm_output_repair.extract_json import _decode_transport_escapes
        expected={"title":"Бухгалтер", "text":"строка } [", "literal":r"\u0410"}
        raw=json.dumps(expected,ensure_ascii=False)
        self.assertEqual(parse_proposal_json(raw+"}"),expected)
        self.assertEqual(parse_proposal_json(raw+"}} ]"),expected)
        self.assertEqual(_decode_transport_escapes("Бухгалтер"),"Бухгалтер")
        self.assertEqual(_decode_transport_escapes(r"Бухгалтер \u0410"),"Бухгалтер А")
        for bad in (raw+"} {} ",raw+" true",raw+" [1]",'{"a":1,"a":2}}'):
            with self.assertRaises(ValueError):
                parse_proposal_json(bad)
