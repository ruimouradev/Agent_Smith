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


def mbpp_profile(task: MBPPTaskInput):
    pass


def swebench_profile(task: SWEBenchTaskInput):
    pass
