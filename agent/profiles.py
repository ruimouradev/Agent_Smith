from dataclasses import dataclass

from contract import MBPPTaskInput, SWEBenchTaskInput

_MBPP_TEMPLATE = """WIP"""

_SWEBENCH_TEMPLATE = """WIP"""


@dataclass
class Profile:
    """Everything the loop and the budget need for one benchmark."""

    task_id: str
    benchmark: str
    user_prompt: str
    stop: list[str]
    max_obs_chars: int
    max_iterations: int
    max_input_tokens: int
    max_output_tokens: int
    max_seconds: float
    request_timeout: float
    template: str

    def system_prompt(self, manual: str) -> str:
        """Fill the template with the sandbox tool manual."""
        return self.template.format(manual=manual)


def mbpp_profile(task: MBPPTaskInput) -> Profile:
    """
    Build the profile for one MBPP task.
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
        max_iterations=10,
        max_input_tokens=6_000,
        max_output_tokens=1_500,
        max_seconds=110.0,  # margin under the 120s wall clock
        request_timeout=60.0,  # a hung call must leave room to retry
        template=_MBPP_TEMPLATE,
    )


def swebench_profile(task: SWEBenchTaskInput) -> Profile:
    """
    Build the profile for one SWE-bench task.
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
        max_iterations=30,
        max_input_tokens=300_000,
        max_output_tokens=10_000,
        max_seconds=870.0,  # margin under the 900s wall clock
        request_timeout=300.0,  # 300k-token calls can be slow
        template=_SWEBENCH_TEMPLATE,
    )
