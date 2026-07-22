"""End-to-end tests for agent.loop with a fake sandbox and provider.

Everything real except the two integration points: profile, budget
and extract are the production code; only the provider and the
sandbox are scripted fakes.
"""

import json
from types import SimpleNamespace

from agent.budget import Budget
from agent.loop import _LAST_CALL, _truncate, run
from agent.profiles import mbpp_profile
from contract import feedback


class FakeSandbox:
    """Final answer on final_answer(); a long observation otherwise."""

    manual = "MANUAL"

    def run(self, code: str) -> str:
        """Mimic the sandbox contract without executing anything."""
        if "final_answer" in code:
            return feedback.FINAL_PREFIX + "SOLUTION"
        return "obs " + "y" * 700


class ScriptedProvider:
    """Replies from a fixed script; records what each call was given."""

    def __init__(self, texts: list[str],
                 input_costs: list[int] | None = None,
                 output_costs: list[int] | None = None):
        self.texts = list(texts)
        self.input_costs = list(input_costs or [])
        self.output_costs = list(output_costs or [])
        self.calls: list[list[str]] = []
        self.caps: list[int] = []

    def generate(self, messages, stop, max_tokens=None, temperature=None):
        """Pop the next scripted reply and note what the model saw."""
        self.calls.append([m["content"] for m in messages])
        self.caps.append(max_tokens)
        cost = self.input_costs.pop(0) if self.input_costs else 100
        out = self.output_costs.pop(0) if self.output_costs else 10
        return SimpleNamespace(
            text=self.texts.pop(0), input_tokens=cost, output_tokens=out,
            time_ms=1.0, retries=0, api_url="u", model_name="m")


class CrashingProvider:
    """Raises on every call, like an API that is down."""

    def generate(self, messages, stop, max_tokens=None, temperature=None):
        """Always fail."""
        raise RuntimeError("api down")


def budget() -> Budget:
    """A fresh budget with the MBPP limits."""
    return Budget(10, 6_000, 1_500, 120.0)


def test_truncate_short_text_is_untouched():
    """Under the limit nothing changes."""
    assert _truncate("abc", 10) == "abc"


def test_truncate_cuts_and_warns():
    """Over the limit: cut at it and tell the model it was cut."""
    result = _truncate("x" * 700, 600)
    assert result.startswith("x" * 600)
    assert "truncated" in result


def test_truncate_exact_limit_is_untouched():
    """Exactly at the limit is not over it."""
    assert _truncate("x" * 600, 600) == "x" * 600


def test_happy_path(mbpp_task, tmp_path):
    """Two iterations to final_answer: success, metrics and file."""
    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider([
        "```python\nprint(1)\n```",
        "```python\nfinal_answer('ok')\n```",
    ])
    out = run(profile, FakeSandbox(), provider, budget(),
              tmp_path / "solution.json")
    assert out.success
    assert out.solution == "SOLUTION"
    assert out.iterations == 2
    assert out.total_input_tokens == 200
    assert out.steps[-1].sandbox_output.startswith(feedback.FINAL_PREFIX)
    # the trace keeps the full observation, the model a truncated one
    assert "y" * 700 in out.steps[0].sandbox_output
    assert any("truncated" in content for content in provider.calls[1])
    written = json.loads((tmp_path / "solution.json").read_text())
    assert written["task_id"] == "7"


def test_reply_without_code_gets_no_code_feedback(mbpp_task, tmp_path):
    """Prose instead of code: NO_CODE goes back, the run recovers."""
    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider([
        "no code here",
        "```python\nfinal_answer('x')\n```",
    ])
    out = run(profile, FakeSandbox(), provider, budget(),
              tmp_path / "solution.json")
    assert out.steps[0].sandbox_output == feedback.NO_CODE
    assert out.success


def test_provider_crash_still_writes_solution(mbpp_task, tmp_path):
    """An exception becomes an error solution.json, never a crash."""
    profile = mbpp_profile(mbpp_task)
    out = run(profile, FakeSandbox(), CrashingProvider(), budget(),
              tmp_path / "solution.json")
    assert not out.success
    assert "api down" in out.error
    assert (tmp_path / "solution.json").exists()


def test_interrupt_is_recorded_and_still_propagates(mbpp_task, tmp_path):
    """Ctrl+C writes a solution.json that says why, and does not get
    swallowed on the way out."""

    class InterruptingProvider:
        """Raises the exception a Ctrl+C delivers."""

        def generate(self, messages, stop, max_tokens=None, temperature=None):
            """Interrupt the run on the first call."""
            raise KeyboardInterrupt()

    profile = mbpp_profile(mbpp_task)
    try:
        run(profile, FakeSandbox(), InterruptingProvider(), budget(),
            tmp_path / "solution.json")
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("the interrupt was swallowed")
    written = json.loads((tmp_path / "solution.json").read_text())
    assert written["error"].startswith("KeyboardInterrupt")


def test_budget_exhausted_reports_and_warns(mbpp_task, tmp_path):
    """No final_answer in time: the error explains it, and the model
    was told the last iteration was the last."""
    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider(["```python\nprint(1)\n```"] * 3)
    out = run(profile, FakeSandbox(), provider,
              Budget(3, 10**6, 10**6, 60.0), tmp_path / "solution.json")
    assert "Budget exhausted" in out.error
    assert out.iterations == 3
    assert any(_LAST_CALL in content for content in provider.calls[2])


def test_output_cap_follows_the_remaining_budget(mbpp_task, tmp_path):
    """Each call is capped server-side by the smaller of the per-step
    ceiling and what is left to spend."""
    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider([
        "```python\nprint(1)\n```",
        "```python\nfinal_answer('ok')\n```",
    ])
    run(profile, FakeSandbox(), provider, budget(),
        tmp_path / "solution.json")
    cap = profile.max_step_output_tokens
    # the per-step ceiling binds while the total budget is still large
    assert provider.caps == [cap, cap]


def test_step_cap_never_exceeds_the_remaining_budget(mbpp_task, tmp_path):
    """Near the end of the output budget, the remaining total binds
    and the request is capped below the per-step ceiling."""
    profile = mbpp_profile(mbpp_task)
    cap = profile.max_step_output_tokens
    provider = ScriptedProvider(
        ["```python\nprint(1)\n```"] * 2
        + ["```python\nfinal_answer('ok')\n```"],
        output_costs=[cap, cap, 50])
    run(profile, FakeSandbox(), provider, budget(),
        tmp_path / "solution.json")
    # two calls at the ceiling leave less than a ceiling for the third
    left = profile.max_output_tokens - 2 * cap
    assert provider.caps == [cap, cap, left]


def test_totals_never_exceed_the_input_limit(mbpp_task, tmp_path):
    """Steeply growing calls: the loop stops before the call that
    would push total_input_tokens past its limit, instead of making
    it and failing on the final metrics."""
    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider(
        ["```python\nprint(1)\n```"] * 5,
        input_costs=[1_000, 1_500, 2_250, 3_375, 5_000])
    out = run(profile, FakeSandbox(), provider, budget(),
              tmp_path / "solution.json")
    assert out.total_input_tokens <= 6_000
    assert not out.success


def test_last_call_warning_is_not_repeated(mbpp_task, tmp_path):
    """is_last() true for several turns must inject one warning, not
    one per turn."""

    class AlwaysLastBudget(Budget):
        """is_last() from the second iteration on, forever."""

        def is_last(self) -> bool:
            """Stay 'last' once reached."""
            return self.iterations >= 1

    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider(["```python\nprint(1)\n```"] * 4)
    run(profile, FakeSandbox(), provider,
        AlwaysLastBudget(4, 10**6, 10**6, 60.0),
        tmp_path / "solution.json")
    assert provider.calls[-1].count(_LAST_CALL) <= 1


def test_old_turns_leave_the_conversation(mbpp_task, tmp_path):
    """A long run drops its earliest attempts, so the input cost of
    the next call stops growing with every turn."""
    profile = mbpp_profile(mbpp_task)
    provider = ScriptedProvider(["```python\nprint(1)\n```"] * 8)
    run(profile, FakeSandbox(), provider, Budget(8, 10**6, 10**6, 60.0),
        tmp_path / "solution.json")
    sent = provider.calls[-1]
    # the opening always travels: the model keeps the task in view
    assert sent[0] == profile.system_prompt(FakeSandbox.manual)
    assert sent[1] == profile.user_prompt
    assert len(sent) == profile.max_turns + 2
