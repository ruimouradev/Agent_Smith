"""
What changes between MBPP and SWE-bench: prompt, limits and formats.

A Profile carries everything benchmark-specific; the loop and the
budget only read it. The MBPP prompt is deliberately spartan (its
tokens are paid on every iteration of a tight budget); the SWE-bench
one can afford method.
"""

from dataclasses import dataclass

from contract import MBPPTaskInput, SWEBenchTaskInput

_MBPP_TEMPLATE = """You are a Python coding agent. Solve the task in \
as few steps as possible.

Each turn: one short thought (two sentences at most), then exactly \
one ```python code block.
The code runs in a sandbox; its output comes back next turn as
Observation. Never write the Observation yourself.

{manual}

Rules:
- Every block tests and submits: call run_tests, then final_answer if
  it reports success. A failing answer is refused and you keep going,
  so holding it back for another turn gains nothing.
- Only printed output reaches you, so wrap calls in print(...).
- The tests you are shown are a sample. Write the function the
  description asks for, using every parameter it declares, general
  enough to hold for inputs the sample does not cover.
"""

_SWEBENCH_TEMPLATE = """You are an autonomous software engineer. You \
work on a real repository mounted at /testbed inside a container.

Each turn: one short thought, then exactly one ```python code block.
The code runs in a sandbox; its output comes back next turn as
Observation. Never write the Observation yourself.

{manual}

Method:
1. Read the issue and find the relevant code (search, read files).
2. Understand the cause before editing. Reproduce it if you can.
3. Make the smallest fix that solves the issue.
4. Run the tests that cover the change.
5. Call get_patch to collect your diff, then final_answer with the
   patch string.

Rules:
- Fix the cause, not the symptom. Do not touch unrelated code.
- If a step fails, read the error before trying again.
"""


@dataclass
class Profile:
    """Everything the loop and the budget need for one benchmark."""

    task_id: str
    benchmark: str
    user_prompt: str
    stop: list[str]
    max_obs_chars: int
    # how many of the latest messages travel with each call, on top
    # of the system prompt and the task statement
    max_turns: int
    max_iterations: int
    max_input_tokens: int
    max_output_tokens: int
    # output ceiling for a single request, so part of the output
    # budget always remains for later steps
    max_step_output_tokens: int
    max_seconds: float
    request_timeout: float
    template: str

    def system_prompt(self, manual: str) -> str:
        """Fill the template with the sandbox tool manual."""
        return self.template.format(manual=manual)


def mbpp_profile(task: MBPPTaskInput) -> Profile:
    """
    Build the profile for one MBPP task.

    Args:
        task: The task as dumped by the moulinette.

    Returns:
        A Profile with the spartan prompt and the MBPP limits.
    """
    tests = "\n".join(task.test_imports + task.test_list)
    user_prompt = (
        f"{task.task_definition}\n\n"
        f"Signature: {task.function_definition}\n"
        f"Tests:\n{tests}"
    )
    return Profile(
        task_id=str(task.task_id),
        benchmark="mbpp",
        user_prompt=user_prompt,
        stop=["Observation:", "<end_code>"],
        max_obs_chars=600,
        max_turns=6,
        max_iterations=10,
        max_input_tokens=6_000,
        max_output_tokens=1_500,
        max_step_output_tokens=700,
        max_seconds=110.0,  # margin under the 120s wall clock
        request_timeout=60.0,  # a hung call must leave room to retry
        template=_MBPP_TEMPLATE,
    )


def swe_profile(task: SWEBenchTaskInput) -> Profile:
    """
    Build the profile for one SWE-bench task.

    Args:
        task: The task as dumped by the moulinette.

    Returns:
        A Profile with the method prompt and the SWE-bench limits.
    """
    hints = f"\n\nHints:\n{task.hints_text}" if task.hints_text else ""
    user_prompt = (
        f"Repository: {task.repo}\n\n"
        f"Issue:\n{task.problem_statement}{hints}"
    )
    return Profile(
        task_id=task.instance_id,
        benchmark="swebench",
        user_prompt=user_prompt,
        stop=["Observation:", "<end_code>"],
        max_obs_chars=3_000,
        max_turns=20,
        max_iterations=30,
        max_input_tokens=300_000,
        max_output_tokens=10_000,
        max_step_output_tokens=2_000,
        max_seconds=870.0,  # margin under the 900s wall clock
        request_timeout=300.0,  # 300k-token calls can be slow
        template=_SWEBENCH_TEMPLATE,
    )
