"""Tests for agent.extract: every accepted format, every rejection."""

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