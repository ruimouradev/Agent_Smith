

import time
from pathlib import Path

from agent.extract import extract
from contract import SolutionOutput, StepMetrics, feedback
from contract.protocols import Sandbox


def run(profile, sandbox: Sandbox, provider, budget,
        output_path: str | Path) -> SolutionOutput:
    start = time.monotonic()
    system_prompt = profile.system_prompt(sandbox.manual)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": profile.user_prompt},
    ]
    steps: list[StepMetrics] = []
    solution = ""

    try:
        while budget.allows():
            if budget.is_last():
                messages.append({"role": "user", "content": _LAST_CALL})

            reply = provider.generate(messages, profile.stop)
            budget.spend(reply)

            code = extract(reply.text)
            if code is None:
                observation = feedback.NO_CODE
            else:
                observation = sandbox.run(code)

    except Exception as exc:  # never crash: report it in solution.json
        error = f"{type(exc).__name__}: {exc}"
    return