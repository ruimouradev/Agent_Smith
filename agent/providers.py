"""
LLM access: one OpenAI-compatible endpoint, several keys, retries.

generate() returns a Reply: the receipt the loop feeds to the budget
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

from openai import APIError, APIStatusError, OpenAI, RateLimitError


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
                 max_attempts: int = 20, pause_seconds: float = 5.0,
                 timeout_seconds: float = 100.0):
        """
        Set up the endpoint and its keys.

        Args:
            base_url: OpenAI-compatible API base URL.
            model: Model identifier to request.
            keys: API keys, tried in rotation on rate limits.
            max_attempts: Give up after this many tries per call.
            pause_seconds: Pause before retrying a transient error.
            timeout_seconds: Abort one call after this long; a hung
                request must never eat the task clock.
        """
        if not keys:
            raise ValueError("no API keys given")
        self.base_url = base_url
        self.model = model
        self.keys = keys
        self.active = 0
        self.max_attempts = max_attempts
        self.pause_seconds = pause_seconds
        self.timeout_seconds = timeout_seconds
        self._client = self._build_client()

    def generate(self, messages: list[dict], stop: list[str],
                 max_tokens: int | None = None) -> Reply:
        """
        Ask the model for the next reply.

        Args:
            messages: The conversation so far (role/content dicts).
            stop: Stop sequences that end the generation.
            max_tokens: Server-side cap on the reply length, so one
                call can never blow the cumulative output limit.

        Returns:
            A Reply with the text and its cost, taken from the
            server-side usage counts.
        """
        kwargs: dict = {"model": self.model, "messages": cast(Any, messages),
                        "stop": stop, "max_tokens": max_tokens}
        start = time.monotonic()
        attempts = 0
        while True:
            try:
                response = self._client.chat.completions.create(**kwargs)
                break
            except RateLimitError:
                attempts += 1
                if attempts >= self.max_attempts:
                    raise
                self._rotate()  # the next key has its own quota
                if attempts % len(self.keys) == 0:
                    # a full lap over the keys: all of them are
                    # rate-limited, so pause before the next round
                    time.sleep(self.pause_seconds)
            except APIStatusError as exc:
                # a 4xx answers the same on every attempt, and asking
                # again spends the time budget on a settled result
                if exc.status_code < 500:
                    raise
                attempts += 1
                if attempts >= self.max_attempts:
                    raise
                time.sleep(self.pause_seconds)
            except APIError:
                attempts += 1
                if attempts >= self.max_attempts:
                    raise
                time.sleep(self.pause_seconds)
        # no choices means no answer to act on, so this still fails loud
        if not response.choices:
            raise RuntimeError(f"{self.base_url} returned no choices")
        text = _as_text(response.choices[0].message.content)
        usage = response.usage
        # some endpoints (seen on the OpenRouter free tier) return no
        # usage counts. The counts are estimated from the text so a
        # missing metric never ends the task. Mistral always reports
        # usage, so the graded path keeps its exact numbers.
        if usage is not None:
            input_tokens = usage.prompt_tokens
            output_tokens = usage.completion_tokens
        else:
            input_tokens = sum(_estimate_tokens(_as_text(m.get("content")))
                               for m in messages)
            output_tokens = _estimate_tokens(text)
        return Reply(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            time_ms=(time.monotonic() - start) * 1000,
            retries=attempts,
            api_url=self.base_url,
            model_name=self.model,
        )

    def _build_client(self) -> OpenAI:
        """Build the client for the active key."""
        # built once and reused: a client per call would redo the
        # TLS handshake every time, a real cost on the 120s clock.
        # max_retries=0: the SDK's own retries (2, with backoff, on
        # the same key) would delay the key rotation done in generate().
        return OpenAI(base_url=self.base_url,
                      api_key=self.keys[self.active],
                      max_retries=0,
                      timeout=self.timeout_seconds)

    def _rotate(self) -> None:
        """Switch to the next key (circular: k1 -> k2 -> ... -> k1)."""
        self.active = (self.active + 1) % len(self.keys)
        self._client = self._build_client()


def _estimate_tokens(text: str) -> int:
    """Approximate a token count from length, about four chars per token.

    Used only when an endpoint omits usage, to keep the run alive with a
    rough figure instead of no figure at all.
    """
    return max(1, len(text) // 4)


def _as_text(content) -> str:
    """
    Reduce a message's content to plain text.

    Most providers return a string (or None). Reasoning models return
    a list of typed blocks; the text ones are joined and the rest, such
    as thinking blocks, are dropped. Anything else degrades to an empty
    string, so an unexpected shape never crashes the run.
    """
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "".join(parts)
    return ""


def from_config(path: str | Path, model: str | None = None,
                base_url: str | None = None,
                timeout_seconds: float = 100.0) -> Provider:
    """
    Build a Provider from configs/models.json and the environment.

    Picks the first provider in the file whose environment variable
    holds at least one key, so a missing key falls through to the
    next provider instead of failing.

    Args:
        path: The models.json file.
        model: Optional override (the --model-name CLI flag).
        base_url: Optional override (the --provider-url CLI flag).
        timeout_seconds: Per-call timeout, sized to the benchmark.

    Returns:
        A ready Provider.
    """
    config = json.loads(Path(path).read_text())
    entries = config["providers"]
    if base_url:
        # the endpoint selects the entry, so the keys that travel are
        # the ones configured for that endpoint
        entries = [e for e in entries if e["base_url"] == base_url]
        if not entries:
            raise RuntimeError(f"no provider configured for {base_url}")
    for entry in entries:
        keys = _split_keys(entry["keys_env"])
        if keys:
            return Provider(
                base_url=entry["base_url"],
                model=model or entry["model"],
                keys=keys,
                timeout_seconds=timeout_seconds,
            )
    names = ", ".join(e["keys_env"] for e in entries)
    raise RuntimeError(f"no API keys found; set one of: {names}")


def _split_keys(name: str) -> list[str]:
    """Read one env var; several keys are separated by commas."""
    raw = os.environ.get(name, "")
    return [key.strip() for key in raw.split(",") if key.strip()]
