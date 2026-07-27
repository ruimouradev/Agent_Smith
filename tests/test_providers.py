"""Tests for agent.providers: key handling, rotation and fail-loud."""

import json

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai import InternalServerError, NotFoundError, RateLimitError

from agent.providers import Provider, _split_keys, from_config

MODELS_JSON = Path(__file__).resolve().parents[1] / "configs/models.json"


def rate_limit_error() -> RateLimitError:
    """Build a RateLimitError without a real HTTP response behind it."""
    return RateLimitError.__new__(RateLimitError)


def not_found_error() -> NotFoundError:
    """Build a NotFoundError without a real HTTP response behind it.

    The retry logic reads status_code, so the bare instance carries it.
    """
    exc = NotFoundError.__new__(NotFoundError)
    exc.status_code = 404
    return exc


def server_error() -> InternalServerError:
    """Build an InternalServerError without a real HTTP response behind it.

    The retry logic reads status_code, so the bare instance carries it.
    """
    exc = InternalServerError.__new__(InternalServerError)
    exc.status_code = 500
    return exc


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
    """Commas separate keys, and whitespace and empties are dropped."""
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


def test_from_config_falls_through_and_takes_the_model_override(monkeypatch):
    """No key for the first provider: the next one is used, and the
    model override wins over the file."""
    for var in ALL_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    provider = from_config(MODELS_JSON, model="X")
    assert provider.model == "X"
    assert provider.keys == ["gk"]
    assert "groq" in provider.base_url


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


def test_missing_usage_is_estimated_not_fatal():
    """An endpoint that omits usage must not end the run: the counts are
    estimated from the text instead of raising."""
    no_usage = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="hello world"))],
        usage=None)
    provider = stubbed_provider(ChatStub(response=no_usage), ["k"])
    reply = provider.generate(
        [{"role": "user", "content": "hi there"}], stop=[])
    assert reply.text == "hello world"
    assert reply.input_tokens >= 1
    assert reply.output_tokens >= 1


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


def test_provider_url_selects_its_own_keys(tmp_path, monkeypatch):
    """The chosen endpoint brings its own key, instead of the first
    configured provider's."""
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"providers": [
        {"name": "a", "base_url": "https://a/v1", "model": "ma",
         "keys_env": "KEY_A"},
        {"name": "b", "base_url": "https://b/v1", "model": "mb",
         "keys_env": "KEY_B"},
    ]}))
    monkeypatch.setenv("KEY_A", "ka")
    monkeypatch.setenv("KEY_B", "kb")
    provider = from_config(config, base_url="https://b/v1")
    assert provider.keys == ["kb"]
    assert provider.model == "mb"


def test_unknown_provider_url_is_rejected(tmp_path, monkeypatch):
    """An endpoint with no entry raises, instead of borrowing another
    provider's credentials."""
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"providers": [
        {"name": "a", "base_url": "https://a/v1", "model": "ma",
         "keys_env": "KEY_A"},
    ]}))
    monkeypatch.setenv("KEY_A", "ka")
    with pytest.raises(RuntimeError, match="no provider configured"):
        from_config(config, base_url="https://nowhere/v1")


def test_client_error_is_not_retried(monkeypatch):
    """A 404 answers the same every time, so it must not spend the
    time budget being asked again."""
    provider = Provider("http://u", "m", ["k"], pause_seconds=0.0)
    calls = []

    def always_404(**kwargs):
        calls.append(1)
        raise not_found_error()

    monkeypatch.setattr(provider._client.chat.completions, "create",
                        always_404)
    with pytest.raises(NotFoundError):
        provider.generate([{"role": "user", "content": "x"}], [])
    assert len(calls) == 1


def test_server_error_is_retried(monkeypatch):
    """A 500 may be transient, so it is worth asking again."""
    provider = Provider("http://u", "m", ["k"], max_attempts=3,
                        pause_seconds=0.0)
    calls = []

    def always_500(**kwargs):
        calls.append(1)
        raise server_error()

    monkeypatch.setattr(provider._client.chat.completions, "create",
                        always_500)
    with pytest.raises(InternalServerError):
        provider.generate([{"role": "user", "content": "x"}], [])
    assert len(calls) == 3


def test_reasoning_block_content_becomes_text():
    """A reasoning model returns typed blocks. The reply carries the
    text ones joined, and never the raw list that would crash extract."""
    blocks = [{"type": "thinking", "thinking": "hmm"},
              {"type": "text", "text": "def f(): return 1"}]
    msg = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=blocks))],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3))
    provider = stubbed_provider(ChatStub(response=msg), ["k"])
    assert provider.generate([], stop=[]).text == "def f(): return 1"
