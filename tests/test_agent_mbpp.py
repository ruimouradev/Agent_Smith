"""Tests for the MBPP entry point wrappers around client and sandbox."""

import json

from agent_mbpp import _CheckedAnswer, _PinnedTests
from contract import feedback


class FakeClient:
    """Records the calls it receives and answers a fixed verdict."""

    def __init__(self, success: bool = True):
        """Choose the verdict every call will get."""
        self.success = success
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict) -> str:
        """Note the call and report the configured verdict."""
        self.calls.append((name, arguments))
        return json.dumps({"success": self.success, "output": ""})


class FakeSandbox:
    """Returns whatever observation the test hands it."""

    manual = "MANUAL"

    def __init__(self, observation: str):
        """Fix the observation every run will return."""
        self.observation = observation

    def run(self, code: str) -> str:
        """Ignore the code and return the scripted observation."""
        return self.observation


def test_pinned_tests_replaces_the_models_test_list():
    """Whatever the model passes, the task's asserts are what run."""
    client = FakeClient()
    _PinnedTests(client, ["assert f(1) == 2"]).call_tool(
        "run_tests", {"code": "x", "test_list": ["assert True"]})
    assert client.calls[0][1]["test_list"] == ["assert f(1) == 2"]


def test_pinned_tests_forwards_other_tools_untouched():
    """Only run_tests is rewritten."""
    client = FakeClient()
    _PinnedTests(client, ["assert f(1) == 2"]).call_tool("other", {"a": 1})
    assert client.calls[0] == ("other", {"a": 1})


def test_passing_answer_reaches_the_loop():
    """A verified answer keeps its marker, so the run ends."""
    observation = feedback.FINAL_PREFIX + "def f(x): return x"
    out = _CheckedAnswer(FakeSandbox(observation), FakeClient(True)).run("c")
    assert out == observation


def test_failing_answer_loses_its_marker():
    """A wrong answer comes back as an observation, not a solution."""
    observation = "log line\n" + feedback.FINAL_PREFIX + "def f(x): return x"
    out = _CheckedAnswer(FakeSandbox(observation), FakeClient(False)).run("c")
    assert feedback.FINAL_PREFIX not in out
    assert out.startswith("log line")


def test_answer_is_checked_against_the_task():
    """The submitted source is what gets tested."""
    client = FakeClient(True)
    observation = feedback.FINAL_PREFIX + "def f(x): return x"
    _CheckedAnswer(FakeSandbox(observation), client).run("c")
    assert client.calls[0][1]["code"] == "def f(x): return x"


def test_observation_without_an_answer_is_untouched():
    """No marker means no extra tool call."""
    client = FakeClient(True)
    out = _CheckedAnswer(FakeSandbox("just output"), client).run("c")
    assert out == "just output"
    assert client.calls == []
