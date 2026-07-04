"""Tests for the MCP server (feat/mcp) — no key, no network, no host.

The server is a thin protocol skin over functions we can call directly:
do_search and do_ask. A fake provider that streams a *poisoned* answer proves
the guardrail wiring (v06) without a model; patched retrieval proves the
search formatting without an index. Needs the `mcp` package installed (it's
in requirements.txt) but never a key — FastMCP is imported, not spoken to.
"""

import asyncio
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo.ops import Budget, BudgetExceeded, ResponseCache  # noqa: E402


class FakeProvider:
    """Streams a canned answer and remembers the messages it was sent."""

    def __init__(self, answer, name="fake"):
        self.answer = answer
        self.name = name
        self.model = "fake-model"
        self.usage = (0, 0)
        self.calls = 0
        self.seen_messages = None

    def complete(self, messages):
        self.calls += 1
        self.seen_messages = messages
        yield self.answer


@unittest.skipUnless(importlib.util.find_spec("mcp"), "mcp SDK not installed")
class TestMCPServer(unittest.TestCase):
    def setUp(self):
        os.environ["PROVIDER"] = "mock"
        from askrepo import mcp_server

        self.server = mcp_server
        # fresh session, memory-only cache: tests never touch the real disk cache
        self.server._config = None
        self.server._budget = None
        self.server._cache = ResponseCache(path=None)

    def test_both_tools_are_advertised(self):
        tools = asyncio.run(self.server.mcp.list_tools())
        self.assertEqual({t.name for t in tools}, {"ask", "search"})
        for t in tools:
            self.assertTrue(t.description)  # the description IS the interface

    def test_mock_ask_answers_offline(self):
        out = self.server.do_ask("hello over mcp")
        self.assertIn("[mock]", out)
        self.assertIn("hello over mcp", out)

    def test_ask_hardens_the_system_prompt(self):
        fake = FakeProvider("fine (x.md:1)", name="mock")  # mock name: no retrieval
        self.server.do_ask("q", provider=fake)
        system = fake.seen_messages[0]
        self.assertEqual(system["role"], "system")
        self.assertIn("UNTRUSTED DATA", system["content"])  # v06 notice rode along

    def test_ask_sanitizes_a_poisoned_answer(self):
        poisoned = (
            "Run `nimbus serve` (README.md:3). "
            "![beacon](https://evil.example/x.png) "
            "[recover your account](https://evil.example/login)"
        )
        fake = FakeProvider(poisoned, name="mock")
        with redirect_stderr(io.StringIO()):
            out = self.server.do_ask("how do I start it?", provider=fake)
        self.assertNotIn("evil.example", out)
        self.assertIn("[external content removed by guardrail]", out)
        self.assertIn("nimbus serve", out)  # the real answer survives

    def test_ask_caches_and_serves_the_second_call_free(self):
        fake = FakeProvider("cached answer (x.md:1)")  # non-mock name: cacheable
        with patch("askrepo.answer.prepare", return_value=([
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q"},
        ], [])), redirect_stderr(io.StringIO()):
            first = self.server.do_ask("same question", provider=fake)
            second = self.server.do_ask("same question", provider=fake)
        self.assertEqual(first, second)
        self.assertEqual(fake.calls, 1)  # second answer never touched the model

    def test_ask_refuses_when_over_budget(self):
        self.server._session()  # create the session budget...
        self.server._budget = Budget(0.001)
        self.server._budget.record(0.002)  # ...and blow past it
        fake = FakeProvider("should never run", name="mock")
        with self.assertRaises(BudgetExceeded):
            self.server.do_ask("one more", provider=fake)
        self.assertEqual(fake.calls, 0)

    def test_search_returns_citation_ready_blocks(self):
        chunk = {"path": "rag-deep-dive/README.md", "text": "alpha\nbeta",
                 "start_line": 41, "end_line": 42}
        with patch("askrepo.retrieve.load_index", return_value={"chunks": []}), \
             patch("askrepo.retrieve.retrieve", return_value=[(0.83, chunk)]):
            out = self.server.do_search("alpha")
        self.assertIn("UNTRUSTED", out)          # labeled as data, not instructions
        self.assertIn("[score 0.83]", out)
        self.assertIn('path="rag-deep-dive/README.md"', out)
        self.assertIn("41| alpha", out)          # citations can point at real lines

    def test_search_without_an_index_errors_in_band(self):
        with patch("askrepo.retrieve.load_index",
                   side_effect=SystemExit("No index found.")):
            with self.assertRaises(RuntimeError):  # not SystemExit: server survives
                self.server.do_search("anything")


if __name__ == "__main__":
    unittest.main()
