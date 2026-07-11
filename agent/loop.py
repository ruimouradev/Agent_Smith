"""
The agent loop: Thought -> Code -> Observation until final_answer.

run() drives one whole task: asks the provider for a reply, extracts
the code, executes it in the sandbox and feeds the observation back,
while the budget allows. Whatever happens — success, budget exhausted
or an exception — a valid solution.json is always written.
"""

import time
from pathlib import Path

from agent.extract import extract
from contract import SolutionOutput, StepMetrics, feedback
from contract.protocols import Sandbox

_LAST_CALL = (
    "This is your last iteration. Call final_answer with your best "
    "solution now."
)


def run(profile, sandbox: Sandbox, provider, budget,
        output_path: str | Path) -> SolutionOutput:
    """
    Drive one task from the first prompt to solution.json.

    Args:
        profile: Benchmark-specific data: prompts, stop sequences,
            observation size limit and how to read the final answer.
        sandbox: Where the extracted code runs.
        provider: LLM access; generate(messages, stop, max_tokens)
            returns a reply carrying the text and its cost.
        budget: Iteration/token/time limits for this run.
        output_path: Where to write the solution.json.

    Returns:
        The SolutionOutput that was written, valid even on failure.
    """
    start = time.monotonic()
    system_prompt = profile.system_prompt(sandbox.manual)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": profile.user_prompt},
    ]
    steps: list[StepMetrics] = []
    solution = ""
    success = False
    error = None
    warned = False

    try:
        while budget.allows():
            if budget.is_last() and not warned:
                # is_last() can stay true for several turns; the
                # warning enters the conversation only once
                messages.append({"role": "user", "content": _LAST_CALL})
                warned = True

            # the server cuts the reply at the remaining output
            # budget, so the output total can never exceed its limit
            reply = provider.generate(messages, profile.stop,
                                      budget.remaining_output())
            budget.spend(reply)

            code = extract(reply.text)
            if code is None:
                observation = feedback.NO_CODE
            else:
                observation = sandbox.run(code)

            final = observation.startswith(feedback.FINAL_PREFIX)
            steps.append(StepMetrics(
                step=len(steps) + 1,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                request_time_ms=reply.time_ms,
                api_url=reply.api_url,
                model_name=reply.model_name,
                llm_output=reply.text,
                sandbox_input=code or "",
                sandbox_output=observation,
                retries=reply.retries,
            ))

            if final:
                solution = observation[len(feedback.FINAL_PREFIX):]
                success = True
                break

            messages.append({"role": "assistant", "content": reply.text})
            messages.append({
                "role": "user",
                "content": _truncate(observation, profile.max_obs_chars),
            })

        if not success and error is None:
            error = "Budget exhausted before final_answer."
    except Exception as exc:  # never crash: report it in solution.json
        error = f"{type(exc).__name__}: {exc}"
    finally:
        result = SolutionOutput.from_steps(
            task_id=profile.task_id,
            benchmark=profile.benchmark,
            success=success,
            solution=solution,
            steps=steps,
            total_time_seconds=time.monotonic() - start,
            system_prompt=system_prompt,
            error=error,
        )
        Path(output_path).write_text(result.model_dump_json(indent=2))
    return result


def _truncate(text: str, limit: int) -> str:
    """Cut an observation and tell the model it was cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + feedback.TRUNCATED.format(chars=limit)
