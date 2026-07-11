"""
LLM access: one OpenAI-compatible endpoint, several keys, retries.

generate() returns a Reply — the receipt the loop feeds to the budget
and the step metrics. Keys rotate on rate limits; transient errors are
retried with a short pause; after max_attempts the exception rises and
the loop turns it into an error solution.json.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from openai import APIError, OpenAI, RateLimitError


@dataclass
class Reply:
    """What one LLM call produced and what it cost."""

    text: str
    input_tokens: int
    output_tokens: int
    time_ms: float
    retries: int
    api_url: str
    model_name: str


class Provider:
    """One OpenAI-compatible endpoint with rotating API keys."""

    def __init__(self, base_url: str, model: str, keys: list[str],
                 max_attempts: int = 4, pause_seconds: float = 1.5):
        """
        Set up the endpoint and its keys.

        Args:
            base_url: OpenAI-compatible API base URL.
            model: Model identifier to request.
            keys: API keys, tried in rotation on rate limits.
            max_attempts: Give up after this many tries per call.
            pause_seconds: Pause before retrying a transient error.
        """
        if not keys:
            raise ValueError("no API keys given")
        self.base_url = base_url
        self.model = model
        self.keys = keys
        self.active = 0
        self.max_attempts = max_attempts
        self.pause_seconds = pause_seconds
        self._client = self._build_client()

    def generate(self, messages: list[dict], stop: list[str]) -> Reply:
        """
        Ask the model for the next reply.

        Args:
            messages: The conversation so far (role/content dicts).
            stop: Stop sequences that end the generation.

        Returns:
            A Reply with the text and its cost, taken from the
            server-side usage counts.
        """
        start = time.monotonic()
        attempts = 0
        while True:
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=cast(Any, messages),
                    stop=stop,
                )
                break
            except RateLimitError:
                attempts += 1
                if attempts >= self.max_attempts:
                    raise
                self._rotate()  # the next key has its own quota
                if attempts % len(self.keys) == 0:
                    # a full lap: every key is limited, so waiting
                    # is all that is left
                    time.sleep(self.pause_seconds)
            except APIError:
                attempts += 1
                if attempts >= self.max_attempts:
                    raise
                time.sleep(self.pause_seconds)
        usage = response.usage
        # metrics are mandatory: fail loudly, not with fabricated 0s
        if usage is None:
            raise RuntimeError(f"{self.base_url} returned no usage counts")
        # some providers return no choices on filtered/failed generations
        if not response.choices:
            raise RuntimeError(f"{self.base_url} returned no choices")
        return Reply(
            # the SDK may give content=None; extract() handles ""
            text=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            time_ms=(time.monotonic() - start) * 1000,
            retries=attempts,
            api_url=self.base_url,
            model_name=self.model,
        )

    def _build_client(self) -> OpenAI:
        """Build the client for the active key."""
        # built once and reused: a client per call would redo the
        # TLS handshake every time, a real cost on the 120s clock
        return OpenAI(base_url=self.base_url,
                      api_key=self.keys[self.active])

    def _rotate(self) -> None:
        """Switch to the next key (circular: k1 -> k2 -> ... -> k1)."""
        self.active = (self.active + 1) % len(self.keys)
        self._client = self._build_client()


def from_config(path: str | Path, model: str | None = None,
                base_url: str | None = None) -> Provider:
    """
    Build a Provider from configs/models.json and the environment.

    Picks the first provider in the file whose environment variable
    holds at least one key, so a missing key falls through to the
    next provider instead of failing.

    Args:
        path: The models.json file.
        model: Optional override (the evaluation's --model-name).
        base_url: Optional override (the evaluation's --provider-url).

    Returns:
        A ready Provider.
    """
    config = json.loads(Path(path).read_text())
    for entry in config["providers"]:
        keys = _split_keys(entry["keys_env"])
        if keys:
            return Provider(
                base_url=base_url or entry["base_url"],
                model=model or entry["model"],
                keys=keys,
            )
    names = ", ".join(e["keys_env"] for e in config["providers"])
    raise RuntimeError(f"no API keys found; set one of: {names}")


def _split_keys(name: str) -> list[str]:
    """Read one env var; several keys are separated by commas."""
    raw = os.environ.get(name, "")
    return [key.strip() for key in raw.split(",") if key.strip()]
