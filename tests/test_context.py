"""Tests for ext-context: token budget, chunk survival, compaction, chat CLI.

All offline: token math is pure, assembly and the chunk pool are pure data,
compaction runs on the deterministic summarizer, and the chat CLI runs on the
mock. No key, no network, no index.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo import tokens  # noqa: E402
from askrepo.assemble import ChunkPool, Section, assemble  # noqa: E402
from askrepo.chat import new_session, respond  # noqa: E402
from askrepo.cli import main  # noqa: E402
from askrepo.memory import ChatMemory  # noqa: E402
from askrepo.providers import MockProvider  # noqa: E402


def chunk(path, line, text):
    return {"path": path, "start_line": line, "end_line": line, "text": text}


class TestTokens(unittest.TestCase):
    def test_estimate_scales_with_length(self):
        self.assertEqual(tokens.estimate(""), 0)
        self.assertEqual(tokens.estimate("a" * 40), 10)  # ~4 chars/token

    def test_fits_accounts_for_system_and_overhead(self):
        msgs = [{"role": "user", "content": "a" * 40}]  # 10 + 4 overhead
        self.assertTrue(tokens.fits(msgs, 20, system=""))
        self.assertFalse(tokens.fits(msgs, 20, system="b" * 40))  # +10 tips it over


class TestAssemble(unittest.TestCase):
    def test_small_high_priority_beats_giant_low_priority(self):
        secs = [Section("big", "x" * 400, 1.0),
                Section("a", "aaaa", 5.0), Section("b", "bbbb", 4.0)]
        r = assemble(secs, budget_tokens=5)
        self.assertEqual({s.label for s in r.kept}, {"a", "b"})
        self.assertEqual([s.label for s in r.dropped], ["big"])
        self.assertLessEqual(r.tokens_used, 5)

    def test_ties_keep_the_earlier_section(self):
        secs = [Section("first", "aaaa", 1.0), Section("second", "bbbb", 1.0)]
        r = assemble(secs, budget_tokens=1)  # only one fits
        self.assertEqual([s.label for s in r.kept], ["first"])


class TestChunkPoolSurvival(unittest.TestCase):
    def test_a_chunk_the_user_keeps_circling_stays_hot(self):
        pool = ChunkPool(decay=0.5)
        pool.add([(0.9, chunk("a.md", 1, "alpha"))], turn=1)
        pool.add([(0.9, chunk("b.md", 1, "beta"))], turn=2)
        pool.add([(0.9, chunk("a.md", 1, "alpha"))], turn=3)  # re-retrieved: refreshed
        pri = {s.label: s.priority for s in pool.sections(current_turn=3)}
        self.assertGreater(pri["a.md:1"], pri["b.md:1"])  # refreshed a beats aging b

    def test_an_abandoned_chunk_decays_and_is_evicted_under_pressure(self):
        pool = ChunkPool(decay=0.5)
        pool.add([(0.9, chunk("old.md", 1, "x" * 200))], turn=1)
        pool.add([(0.9, chunk("new.md", 1, "y" * 200))], turn=5)  # 4 turns newer
        # a budget big enough for one chunk: the fresh one wins, the old is evicted
        one = tokens.estimate("y" * 200) + 30
        r = assemble(pool.sections(current_turn=5), budget_tokens=one)
        self.assertEqual([s.label for s in r.kept], ["new.md:1"])
        self.assertEqual([s.label for s in r.dropped], ["old.md:1"])

    def test_pool_is_bounded(self):
        pool = ChunkPool(max_pool=3)
        for i in range(6):
            pool.add([(0.5, chunk(f"f{i}.md", 1, "z"))], turn=i)
        self.assertLessEqual(len(pool.sections(current_turn=6)), 3)


class TestCompaction(unittest.TestCase):
    def test_over_budget_turns_compact_into_a_summary(self):
        # a tiny budget forces compaction after a couple of turns
        mem = ChatMemory(budget_tokens=30, keep_recent=2)
        mem.add("user", "my index was built with k=8")   # the early fact
        mem.add("assistant", "noted, k=8")
        for i in range(4):
            mem.add("user", f"unrelated question number {i} padded out a bit")
            mem.add("assistant", f"answer number {i} also padded to spend tokens")
        info = mem.info()
        self.assertGreater(info["compactions"], 0)
        self.assertTrue(info["has_summary"])
        self.assertLessEqual(info["turns_sent"], 4)  # bounded despite 12 turns
        self.assertIn("k=8", mem.summary)  # the early fact survived compaction

    def test_no_compaction_when_it_fits(self):
        mem = ChatMemory(budget_tokens=10_000, keep_recent=2)
        mem.add("user", "hi")
        mem.add("assistant", "hello")
        self.assertEqual(mem.info()["compactions"], 0)
        self.assertFalse(mem.info()["has_summary"])


class TestChatTurn(unittest.TestCase):
    def test_mock_turn_answers_and_records_clean_thread(self):
        prov = MockProvider()
        session = new_session(2000, prov)
        answer, cost = respond(session, "what is barge-in?", prov)
        self.assertIn("[mock]", answer)
        self.assertEqual(cost, 0.0)
        # the thread holds the CLEAN question, not any chunk context
        self.assertEqual(session.memory.turns[0]["content"], "what is barge-in?")
        self.assertEqual(session.memory.turns[1]["role"], "assistant")

    def test_multi_turn_stays_within_its_conversation_budget(self):
        prov = MockProvider()
        session = new_session(600, prov)  # small -> compaction will kick in
        for i in range(8):
            respond(session, f"question {i} with some words to spend tokens", prov)
        self.assertLessEqual(session.memory.info()["turn_tokens"],
                             session.memory.budget)
        self.assertGreater(session.memory.info()["compactions"], 0)


class TestChatCLI(unittest.TestCase):
    def test_oneshot_chat_offline_exits_zero(self):
        os.environ["PROVIDER"] = "mock"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["chat", "hello there corpus"])
        self.assertEqual(code, 0)
        self.assertIn("[mock]", out.getvalue())
        self.assertIn("window", err.getvalue())


if __name__ == "__main__":
    unittest.main()
