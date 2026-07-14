"""Tests for agent.extract: every accepted format, every rejection."""

import pytest

from agent.extract import extract


def test_python_block():
    """The primary format: one fenced python block."""
    assert extract("```python\nx = 1\n```") == "x = 1"


def test_block_without_language_tag():
    """Weak models often drop the language tag; still accepted."""
    assert extract("```\nx = 1\n```") == "x = 1"


def test_py_tag():
    """The short 'py' tag is accepted too."""
    assert extract("```py\nx = 1\n```") == "x = 1"


def test_blocks_are_joined_in_order():
    """Several blocks in one reply become one program."""
    text = "```python\na = 1\n``` then ```python\nb = 2\n```"
    assert extract(text) == "a = 1\nb = 2"


def test_unclosed_block_is_recovered():
    """A reply cut mid-block (token limit or stop) is not wasted."""
    text = "Thought\n```python\ndef f():\n    return 1"
    assert extract(text) == "def f():\n    return 1"


def test_closed_plus_unclosed_block():
    """A closed block followed by a cut one: both recovered."""
    text = "```python\na = 1\n``` x ```python\nb = 2"
    assert extract(text) == "a = 1\nb = 2"


def test_xml_call_becomes_python():
    """An Anthropic-style <invoke> converts to a Python call."""
    text = ('<invoke name="t">'
            '<parameter name="c">x = 1</parameter></invoke>')
    assert extract(text) == "t(c='x = 1')"


def test_json_call_becomes_python():
    """A Hermes-style <tool_call> converts to a Python call."""
    text = '<tool_call>{"name": "f", "arguments": {"a": 1}}</tool_call>'
    assert extract(text) == "f(a=1)"


def test_json_nested_object():
    """Nested JSON arguments survive the conversion."""
    text = ('<tool_call>{"name": "f", "arguments": {"d": {"a": 1}}}'
            '</tool_call>')
    assert extract(text) == "f(d={'a': 1})"


def test_json_bool_and_null_become_python_literals():
    """true/null in JSON become True/None in the generated call."""
    text = ('<tool_call>{"name": "f", "arguments":'
            ' {"b": true, "n": null}}</tool_call>')
    assert extract(text) == "f(b=True, n=None)"


def test_react_call_with_brace_inside_string():
    """raw_decode reads exactly one JSON value: braces in strings
    and trailing prose do not break the parse."""
    text = 'Action: f\nAction Input: {"s": "x}y"} trailing prose'
    assert extract(text) == "f(s='x}y')"


@pytest.mark.parametrize("text", [
    "the answer is 4",
    "",
    "<tool_call>{broken}</tool_call>",
    "<tool_call>[1, 2]</tool_call>",
    "Action: f\nAction Input: nothing",
    "```python\n   \n```",
])
def test_no_code_returns_none(text):
    """Prose, empty and malformed inputs all yield None, never junk."""
    assert extract(text) is None
