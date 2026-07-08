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