"""
LLM access: one OpenAI-compatible endpoint, several keys, retries.
"""
import time
from dataclasses import dataclass
from typing import Any, cast


from openai import APIError, OpenAI, RateLimitError


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

    def __init__(self, base_url: str, model: str, keys: list[str],
                 max_attempts: int = 4, pause_seconds: float = 1.5):
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
        start = time.monotonic()
        attempts = 0
        while 1:
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
                self._rotate()
            except APIError:
                attempts += 1
                if attempts >= self.max_attempts:
                    raise
                time.sleep(self.pause_seconds)
        usage = response.usage
        if usage is None:
            raise RuntimeError(f"{self.base_url} returned no usage counts")

#CHECK LATER: response.choices[0]THIS COULD NOT EXIST, BETTER TO PROTECT!!!
        if not response.choices[0].message.content:
            return

        return Reply(
            text=response.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            time_ms=(time.monotonic() - start) * 1000,
            retries=attempts,
            api_url=self.base_url,
            model_name=self.model,
        )


    def _build_client(self) -> OpenAI:
        return OpenAI(base_url=self.base_url,
                      api_key=self.keys[self.active])

    def _rotate(self) -> None:
        self.active = (self.active + 1) % len(self.keys)
        self._client = self._build_client()


def from_config():
    ...
    raise RuntimeError(f"no API keys found")
