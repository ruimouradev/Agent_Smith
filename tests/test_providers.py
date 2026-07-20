"""Tests for agent.providers: key handling, rotation and fail-loud."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai import RateLimitError

from agent.providers import Provider, _split_keys, from_config

MODELS_JSON = Path(__file__).resolve().parents[1] / "configs/models.json"


def rate_limit_error() -> RateLimitError:
    """Build a RateLimitError without a real HTTP response behind it."""
    return RateLimitError.__new__(RateLimitError)


def ok_response() -> SimpleNamespace:
    """A successful completion with usage counts, as the SDK shapes it."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
    )


class ChatStub:
    """Chat endpoint stub: rate-limits the first n calls, then replies."""

    def __init__(self, fail_first: int = 0, response=None):
        self.fail_first = fail_first
        self.response = ok_response() if response is None else response
        self.calls = 0

    def create(self, **kwargs):
        """Mimic chat.completions.create()."""
        self.calls += 1
        if self.calls <= self.fail_first:
            raise rate_limit_error()
        return self.response


def stubbed_provider(chat: ChatStub, keys: list[str], **kwargs) -> Provider:
    """A Provider whose OpenAI client is replaced by the given stub."""
    provider = Provider("http://test", "model", keys, **kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    # setattr keeps mypy quiet about replacing a method on an instance
    setattr(provider, "_client", client)
    setattr(provider, "_build_client", lambda: client)
    return provider


def test_split_multiple_keys(monkeypatch):
    """Commas separate keys; whitespace and empties are dropped."""
    monkeypatch.setenv("TEST_KEYS", " a, b ,c,")
    assert _split_keys("TEST_KEYS") == ["a", "b", "c"]


def test_split_single_key(monkeypatch):
    """One key, no comma: a one-element list."""
    monkeypatch.setenv("TEST_KEYS", "only-one")
    assert _split_keys("TEST_KEYS") == ["only-one"]


def test_split_missing_var():
    """An unset variable yields an empty list, not an error."""
    assert _split_keys("DOES_NOT_EXIST") == []


ALL_KEY_VARS = ("MISTRAL_API_KEY", "GROQ_API_KEY",
                "GEMINI_API_KEY", "OPENROUTER_API_KEY")


def test_from_config_falls_through_and_takes_overrides(monkeypatch):
    """No key for the first provider: the next one is used; the
    model/url overrides win over the file."""
    for var in ALL_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    provider = from_config(MODELS_JSON, model="X", base_url="http://u")
    assert provider.model == "X"
    assert provider.base_url == "http://u"
    assert provider.keys == ["gk"]


def test_from_config_without_any_key_names_the_vars(monkeypatch):
    """No keys anywhere: the error says which variables to set."""
    for var in ALL_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
        from_config(MODELS_JSON)


def test_empty_keys_rejected():
    """A Provider without keys is a configuration error, caught early."""
    with pytest.raises(ValueError):
        Provider("http://test", "model", [])


def test_rate_limit_rotates_key_and_recovers(monkeypatch):
    """One 429: the next key answers; the receipt counts the retry."""
    monkeypatch.setattr("agent.providers.time.sleep", lambda s: None)
    provider = stubbed_provider(ChatStub(fail_first=1), ["k1", "k2"])
    reply = provider.generate([{"role": "user", "content": "x"}], stop=[])
    assert reply.text == "hi"
    assert reply.retries == 1
    assert provider.active == 1


def test_single_key_waits_between_laps(monkeypatch):
    """With one key every rotation is a full lap: the provider must
    sleep instead of hammering the same limited key."""
    sleeps: list[float] = []
    monkeypatch.setattr("agent.providers.time.sleep", sleeps.append)
    provider = stubbed_provider(ChatStub(fail_first=2), ["only"],
                                max_attempts=4)
    reply = provider.generate([], stop=[])
    assert reply.retries == 2
    assert len(sleeps) >= 2


def test_no_choices_fails_loudly():
    """An empty choices list raises instead of crashing on index 0."""
    empty = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0))
    provider = stubbed_provider(ChatStub(response=empty), ["k"])
    with pytest.raises(RuntimeError, match="no choices"):
        provider.generate([], stop=[])


def test_missing_usage_fails_loudly():
    """Metrics are mandatory: no usage means an error, not zeros."""
    no_usage = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=None)
    provider = stubbed_provider(ChatStub(response=no_usage), ["k"])
    with pytest.raises(RuntimeError, match="usage"):
        provider.generate([], stop=[])


def test_client_owns_no_retries_and_a_finite_timeout():
    """The SDK's own retries are off (they would fight our instant
    key rotation) and a hung request dies at our timeout, not at the
    SDK's 600s default."""
    provider = Provider("http://test", "model", ["k"],
                        timeout_seconds=42.0)
    assert provider._client.max_retries == 0
    assert provider._client.timeout == 42.0


def test_max_tokens_is_forwarded_to_the_api():
    """The output cap given by the loop reaches the create call."""
    class Recording(ChatStub):
        """ChatStub that also keeps the kwargs of each call."""

        def create(self, **kwargs):
            """Record kwargs, then answer normally."""
            self.kwargs = kwargs
            return super().create(**kwargs)

    chat = Recording()
    provider = stubbed_provider(chat, ["k"])
    provider.generate([], stop=[], max_tokens=321)
    assert chat.kwargs["max_tokens"] == 321


def test_none_content_becomes_empty_text():
    """The SDK may give content=None; the Reply carries ''. """
    none_content = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0))
    provider = stubbed_provider(ChatStub(response=none_content), ["k"])
    assert provider.generate([], stop=[]).text == ""
