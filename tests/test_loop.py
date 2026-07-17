import json
from types import SimpleNamespace

from agent.budget import Budget
from agent.loop import _truncate, run
from agent.profiles import mbpp_profile
from contract import feedback


class ScriptedProvider:

    def __init__(self, texts: list[str],
                 input_costs: list[int] | None = None):
        self.texts = list(texts)
        self.input_costs = list(input_costs or [])
        self.calls: list[list[str]] = []
        self.caps: list[int] = []

    def generate(self, messages, stop, max_tokens=None):
        self.calls.append([m["content"] for m in messages])
        self.caps.append(max_tokens)
        cost = self.input_costs.pop(0) if self.input_costs else 100
        return SimpleNamespace(
            text=self.texts.pop(0), input_tokens=cost, output_tokens=10,
            time_ms=1.0, retries=0, api_url="u", model_name="m")


def budget() -> Budget:
    return Budget(10, 6_000, 1_500, 120.0)


def test_truncate_short_text_is_untouched():
    assert _truncate("abc", 10) == "abc"


def test_truncate_cuts_and_warns():
    result = _truncate("x" * 700, 600)
    assert result.startswith("x" * 600)
    assert "truncated" in result


def test_truncate_exact_limit_is_untouched():
    assert _truncate("x" * 600, 600) == "x" * 600


def test_happy_path(mbpp_task, tmp_path):
    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider([
        "```python\nprint(1)\n```",
        "```python\nfinal_answer('ok')\n```",
    ])
    out = run(profile, provider, budget(),
              tmp_path / "solution.json")
    assert out.success
    assert out.solution == "SOLUTION"
    assert out.iterations == 2
    assert out.total_input_tokens == 200
    assert out.steps[-1].sandbox_output.startswith(feedback.FINAL_PREFIX)
    # the trace keeps the full observation
    assert "y" * 700 in out.steps[0].sandbox_output
    assert any("truncated" in content for content in provider.calls[1])
    written = json.loads((tmp_path / "solution.json").read_text())
    assert written["task_id"] == "7"