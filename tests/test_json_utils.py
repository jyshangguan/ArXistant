"""Tests for json_utils module."""

import json

from src.tools.json_utils import parse_llm_json, sanitize_json_escapes


class TestSanitizeJsonEscapes:
    def test_valid_escapes_preserved(self):
        assert sanitize_json_escapes(r'\\n') == r'\\n'

    def test_latex_odot(self):
        s = r'"$M_\odot$ cloud"'
        result = sanitize_json_escapes(s)
        assert r"\\odot" in result

    def test_latex_alpha(self):
        s = r'"$\alpha$ is a constant"'
        result = sanitize_json_escapes(s)
        assert r"\\alpha" in result

    def test_no_backslashes_unchanged(self):
        s = '{"key": "value"}'
        assert sanitize_json_escapes(s) == s


class TestParseLlmJson:
    def test_valid_json(self):
        result = parse_llm_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_latex(self):
        result = parse_llm_json(r'{"x": "$M_\odot$ cloud"}')
        assert "M_" in result.get("x", "")

    def test_code_fence(self):
        text = '```json\n{"points": []}\n```'
        result = parse_llm_json(text)
        assert result == {"points": []}

    def test_code_fence_no_lang(self):
        text = '```\n{"data": 123}\n```'
        result = parse_llm_json(text)
        assert result == {"data": 123}

    def test_brace_extraction(self):
        text = 'Here is the result:\n{"score": 5}\nDone.'
        result = parse_llm_json(text)
        assert result.get("score") == 5

    def test_expected_root(self):
        text = '{"points": [{"id": "P1"}]}'
        result = parse_llm_json(text, expected_root="points")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "P1"

    def test_expected_root_missing(self):
        text = '{"items": []}'
        result = parse_llm_json(text, expected_root="points")
        assert result == {}

    def test_empty_text(self):
        result = parse_llm_json("")
        assert result == {}

    def test_not_json(self):
        result = parse_llm_json("this is not json at all")
        assert result == {}

    def test_latex_in_code_fence(self):
        text = '```json\n{"text": "$\\sigma = 5$ km/s"}\n```'
        result = parse_llm_json(text)
        assert result.get("text", "") != ""

    def test_nested_json(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = parse_llm_json(text)
        assert result["outer"]["inner"] == [1, 2, 3]

    def test_array_root(self):
        text = '```json\n[{"id": 1}, {"id": 2}]\n```'
        result = parse_llm_json(text)
        # Default behavior returns {} since it checks isinstance(result, dict)
        assert result == {}
