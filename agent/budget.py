"""
Tracks the limits of a run and says when it is time to wrap up.

The loop asks allows() before each iteration and is_last() to know
when to tell the model to close with its best answer. spend() takes
the receipt of each LLM call. The budget knows numbers, not
benchmarks: the limits come in through the constructor.
"""

import time


class Budget:
    """Iteration, token and time limits for one run."""

    def __init__(self, max_iterations: int, max_input_tokens: int,
                 max_output_tokens: int, max_seconds: float):
        """
        Store the limits and start the clock.

        Args:
            max_iterations: Maximum agent loop iterations.
            max_input_tokens: Cumulative input token limit.
            max_output_tokens: Cumulative output token limit.
            max_seconds: Wall-clock limit for the whole run.
        """
        self.max_iterations = max_iterations
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.max_seconds = max_seconds
        self.iterations = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.last_input_cost = 0
        self.last_output_cost = 0
        self.last_iter_seconds = 0.0
        self.start = time.monotonic()
        self._mark = self.start

    def allows(self) -> bool:
        """
        Return True while the next iteration still surely fits.

        Predictive where overshooting would fail the run: the context
        only grows, so the next call costs at least what the last one
        did; if even that minimum does not fit in the input or time
        limits, the call is not worth making: the final totals must
        stay within the limits. Output needs no prediction here: the
        provider caps it server-side per call.
        """
        return (self.iterations < self.max_iterations
                and (self.input_tokens + self.last_input_cost
                     <= self.max_input_tokens)
                and self.output_tokens < self.max_output_tokens
                and (self.elapsed() + self.last_iter_seconds
                     < self.max_seconds))

    def is_last(self) -> bool:
        """
        Return True when the next iteration is the last affordable one.

        Three triggers: the iteration count, the remaining input
        tokens, and the remaining output tokens, which the final
        answer also spends. The context only grows (the reply and the
        observation are appended every turn), so the next call costs
        more than the previous one, and 1.5 is that growth margin.
        """
        by_count = self.iterations == self.max_iterations - 1
        left_in = self.max_input_tokens - self.input_tokens
        by_input = 0 < self.last_input_cost * 1.5 >= left_in
        left_out = self.max_output_tokens - self.output_tokens
        by_output = 0 < self.last_output_cost * 1.5 >= left_out
        return by_count or by_input or by_output

    def spend(self, reply) -> None:
        """Count one iteration and add the receipt of its LLM call."""
        now = time.monotonic()
        self.last_iter_seconds = now - self._mark
        self._mark = now
        self.iterations += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        self.last_input_cost = reply.input_tokens
        self.last_output_cost = reply.output_tokens

    def remaining_output(self) -> int:
        """Output tokens still spendable: the hard cap for one call."""
        return self.max_output_tokens - self.output_tokens

    def elapsed(self) -> float:
        """Seconds since the budget was created."""
        return time.monotonic() - self.start
