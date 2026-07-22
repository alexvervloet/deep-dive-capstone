"""Tests for the local (Ollama) provider: offline, no server needed.

The local backend is "the OpenAI SDK pointed at localhost", so we test it by
patching `openai.OpenAI` with a fake that records how it was constructed and
what it was asked. That proves the wiring (base_url, dummy key, model, free
cost, the shared streaming/embedding code) without a running Ollama; the
same no-key discipline as every other tag's suite.
"""

import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import cast  # noqa: E402

from askrepo import providers  # noqa: E402
from askrepo.providers import LocalProvider, cost_usd, embed, get_provider  # noqa: E402


class FakeOpenAI:
    """Records constructor kwargs; serves canned chat + embedding responses."""

    last_kwargs: dict = {}

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

    def test_points_the_sdk_at_the_default_local_server(self):
        with patch("openai.OpenAI", FakeOpenAI), patch.dict(os.environ, {}, clear=False):
            for var in ("LOCAL_BASE_URL", "OLLAMA_HOST"):
                os.environ.pop(var, None)
            list(get_provider("local", model="qwen3").complete(
                [{"role": "user", "content": "hi"}]))
        self.assertEqual(FakeOpenAI.last_kwargs.get("base_url"),
                         "http://localhost:11434/v1")
        self.assertEqual(FakeOpenAI.last_kwargs.get("api_key"), "ollama")

    def test_local_base_url_override_used_verbatim(self):
        # a full URL (LM Studio, vLLM, another machine) is used as-is; no /v1 tacked on
        with patch("openai.OpenAI", FakeOpenAI), \
             patch.dict(os.environ, {"LOCAL_BASE_URL": "http://192.168.1.9:1234/v1",
                                     "LOCAL_API_KEY": "sk-secret"}):
            list(get_provider("local").complete([{"role": "user", "content": "hi"}]))
        self.assertEqual(FakeOpenAI.last_kwargs.get("base_url"), "http://192.168.1.9:1234/v1")
        self.assertEqual(FakeOpenAI.last_kwargs.get("api_key"), "sk-secret")

    def test_ollama_host_still_appends_v1_for_backcompat(self):
        with patch("openai.OpenAI", FakeOpenAI), \
             patch.dict(os.environ, {"OLLAMA_HOST": "http://box:11434"}):
            os.environ.pop("LOCAL_BASE_URL", None)
            list(get_provider("local").complete([{"role": "user", "content": "hi"}]))
        self.assertEqual(FakeOpenAI.last_kwargs.get("base_url"), "http://box:11434/v1")

    def test_openai_provider_still_talks_to_openai_proper(self):
        # the refactor must NOT leak the local base_url into the cloud provider
        with patch("openai.OpenAI", FakeOpenAI):
            list(get_provider("openai").complete([{"role": "user", "content": "hi"}]))
        self.assertEqual(FakeOpenAI.last_kwargs, {})  # no base_url, no dummy key

    def test_local_is_free(self):
        p = cast(LocalProvider, get_provider("local"))
        p.usage = (1000, 1000)
        self.assertEqual(cost_usd(p), 0.0)

    def test_local_gets_generous_reasoning_headroom(self):
        # thinking models spend output tokens reasoning before the answer, so
        # local's budget must exceed the cloud default (a 1024 cap can be fully
        # consumed by reasoning, leaving content empty)
        from askrepo.providers import MAX_TOKENS
        self.assertGreater(cast(LocalProvider, get_provider("local")).max_tokens, MAX_TOKENS)
        with patch.dict(os.environ, {"LOCAL_MAX_TOKENS": "20000"}):
            self.assertEqual(providers.LocalProvider().max_tokens, 20000)

    def test_embeddings_can_target_a_separate_endpoint(self):
        # a runner that serves chat but not embeddings: point embeddings elsewhere
        with patch("openai.OpenAI", FakeOpenAI), \
             patch.dict(os.environ, {"LOCAL_BASE_URL": "http://chat-box:1234/v1",
                                     "LOCAL_EMBED_BASE_URL": "http://embed-box:11434/v1"}):
            vecs, toks = embed(["a", "b"], stack="local")
        self.assertEqual(len(vecs), 2)
        self.assertEqual(FakeOpenAI.last_kwargs.get("base_url"), "http://embed-box:11434/v1")

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
