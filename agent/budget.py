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
        self.start = time.monotonic()

    def allows(self) -> bool:
        """Return True while every limit still has room."""
        return (self.iterations < self.max_iterations
                and self.input_tokens < self.max_input_tokens
                and self.output_tokens < self.max_output_tokens
                and self.elapsed() < self.max_seconds)

    def is_last(self) -> bool:
        """
        Return True when the next iteration is the last affordable one.

        Two triggers: the iteration count, or the remaining input
        tokens. The context only grows (the reply and the observation
        are appended every turn), so the next call costs more than the
        previous one — the 1.5 factor is that growth margin.
        """
        by_count = self.iterations == self.max_iterations - 1
        remaining = self.max_input_tokens - self.input_tokens
        by_tokens = 0 < self.last_input_cost * 1.5 >= remaining
        return by_count or by_tokens

    def spend(self, reply) -> None:
        """Count one iteration and add the receipt of its LLM call."""
        self.iterations += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        self.last_input_cost = reply.input_tokens

    def elapsed(self) -> float:
        """Seconds since the budget was created."""
        return time.monotonic() - self.start
