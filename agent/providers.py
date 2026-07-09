"""
LLM access: one OpenAI-compatible endpoint, several keys, retries.
"""

import time
from dataclasses import dataclass
from typing import cast

from openai import APIError


@dataclass
class Reply:

    text: str
    input_tokens: int
    output_tokens: int
    time_ms: float
    retries: int
    api_url: str
    model_name: str


class Provider:

    def __init__(self, base_url: str, model: str, keys: list[str]):
        if not keys:
            raise ValueError("no API keys given")
        self.base_url = base_url
        self.model = model
        self.keys = keys
        self.active = 0
        self._client = self._build_client()

    def generate(self, messages: list[dict], stop: list[str]) -> Reply:
        start = time.monotonic()
        attempts = 0
        while 1:
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=cast(messages),
                    stop=stop,
                )
                break
            except APIError:
                attempts += 1
                if attempts >= self.max_attempts:
                    raise
                time.sleep(self.pause_seconds)
        usage = response.usage
        return Reply(
            text=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            time_ms=(time.monotonic() - start) * 1000,
            retries=attempts,
            model_name=self.model,
        )
    def _build_client(self):
        ...

    def _rotate(self) -> None:
        self.active = (self.active + 1) % len(self.keys)


def from_config():
    ...
    raise RuntimeError(f"no API keys found")
