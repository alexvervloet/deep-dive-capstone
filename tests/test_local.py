"""Tests for the local (Ollama) provider — offline, no server needed.

The local backend is "the OpenAI SDK pointed at localhost", so we test it by
patching `openai.OpenAI` with a fake that records how it was constructed and
what it was asked. That proves the wiring (base_url, dummy key, model, free
cost, the shared streaming/embedding code) without a running Ollama — the
same no-key discipline as every other tag's suite.
"""

import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo import providers  # noqa: E402
from askrepo.providers import cost_usd, embed, get_provider  # noqa: E402


class FakeOpenAI:
    """Records constructor kwargs; serves canned chat + embedding responses."""

    last_kwargs = None

    def __init__(self, **kwargs):
        FakeOpenAI.last_kwargs = kwargs
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._chat))
        self.embeddings = types.SimpleNamespace(create=self._embed)

    def _chat(self, **kwargs):
        self.chat_kwargs = kwargs
        chunk = types.SimpleNamespace(
            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content="hi"))],
            usage=None)
        final = types.SimpleNamespace(
            choices=[], usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=2))
        return iter([chunk, final])

    def _embed(self, **kwargs):
        self.embed_kwargs = kwargs
        n = len(kwargs["input"])
        return types.SimpleNamespace(
            data=[types.SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in range(n)],
            usage=types.SimpleNamespace(total_tokens=7))


class TestLocalProvider(unittest.TestCase):
    def test_get_provider_returns_local(self):
        p = get_provider("local")
        self.assertEqual(p.name, "local")

    def test_default_model_is_overridable(self):
        self.assertEqual(get_provider("local", model="llama3.2").model, "llama3.2")
        with patch.dict(os.environ, {"LOCAL_MODEL": "phi3.5"}):
            # constructed fresh so it re-reads the env default
            self.assertEqual(providers.LocalProvider().model, "phi3.5")

    def test_points_the_sdk_at_ollama(self):
        with patch("openai.OpenAI", FakeOpenAI):
            list(get_provider("local", model="qwen3").complete(
                [{"role": "user", "content": "hi"}]))
        self.assertEqual(FakeOpenAI.last_kwargs.get("base_url"),
                         providers.LOCAL_BASE_URL)
        self.assertEqual(FakeOpenAI.last_kwargs.get("api_key"), "ollama")

    def test_openai_provider_still_talks_to_openai_proper(self):
        # the refactor must NOT leak the local base_url into the cloud provider
        with patch("openai.OpenAI", FakeOpenAI):
            list(get_provider("openai").complete([{"role": "user", "content": "hi"}]))
        self.assertEqual(FakeOpenAI.last_kwargs, {})  # no base_url, no dummy key

    def test_local_is_free(self):
        p = get_provider("local")
        p.usage = (1000, 1000)
        self.assertEqual(cost_usd(p), 0.0)

    def test_local_embeddings_use_the_local_endpoint_and_model(self):
        with patch("openai.OpenAI", FakeOpenAI):
            vecs, toks = embed(["a", "b"], stack="local")
        self.assertEqual(len(vecs), 2)
        self.assertEqual(FakeOpenAI.last_kwargs.get("base_url"), providers.LOCAL_BASE_URL)

    def test_embed_falls_back_when_usage_missing(self):
        # Ollama sometimes omits usage; embed() must estimate, not crash
        class NoUsage(FakeOpenAI):
            def _embed(self, **kwargs):
                r = super()._embed(**kwargs)
                return types.SimpleNamespace(data=r.data)  # no .usage
        with patch("openai.OpenAI", NoUsage):
            vecs, toks = embed(["hello world"], stack="local")
        self.assertEqual(len(vecs), 1)
        self.assertIsInstance(toks, int)


if __name__ == "__main__":
    unittest.main()
