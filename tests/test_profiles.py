"""Tests for agent.profiles: prompts, limits and the manual hook."""

from agent.profiles import mbpp_profile, swebench_profile


def test_mbpp_task_id_becomes_str(mbpp_task):
    """The int task_id converts once, here, to the str the output
    contract requires."""
    assert mbpp_profile(mbpp_task).task_id == "7"


def test_mbpp_limits(mbpp_task):
    """The MBPP limits, with the wall-clock margin on the seconds."""
    profile = mbpp_profile(mbpp_task)
    assert (profile.max_iterations, profile.max_input_tokens,
            profile.max_output_tokens, profile.max_seconds
            ) == (10, 6_000, 1_500, 110.0)
    assert profile.request_timeout == 60.0


def test_mbpp_tests_reach_the_user_prompt(mbpp_task):
    """The model must see the asserts and their imports."""
    prompt = mbpp_profile(mbpp_task).user_prompt
    assert "assert f() == 1" in prompt
    assert "import math" in prompt


def test_manual_fills_the_template(mbpp_task):
    """system_prompt() injects the tool manual into the template."""
    assert "XYZ_MANUAL" in mbpp_profile(mbpp_task).system_prompt(
        "XYZ_MANUAL")


def test_swebench_limits(swe_task):
    """The SWE-bench limits, with the wall-clock margin."""
    profile = swebench_profile(swe_task)
    assert (profile.max_iterations, profile.max_input_tokens,
            profile.max_output_tokens, profile.max_seconds
            ) == (30, 300_000, 10_000, 870.0)
    assert profile.request_timeout == 300.0


def test_swebench_without_hints(swe_task):
    """No hints in the task: no empty 'Hints:' noise in the prompt."""
    assert "Hints" not in swebench_profile(swe_task).user_prompt


def test_swebench_with_hints(swe_task):
    """Hints, when present, are appended to the prompt."""
    task = swe_task.model_copy(update={"hints_text": "check the cache"})
    assert "Hints:\ncheck the cache" in swebench_profile(task).user_prompt
