"""Tests for chunking, prompt assembly, and the offline CLI path.

These exercise real product code — the same functions v03/v02 answer with —
entirely on the mock. No embeddings, no key, no network.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo import indexer  # noqa: E402
from askrepo.cli import main  # noqa: E402
from askrepo.prompts import DECLINE_PHRASE, build_messages, format_context  # noqa: E402


class TestChunking(unittest.TestCase):
    def test_markdown_splits_at_headings_and_tracks_lines(self):
        text = "# Title\nintro line\n\n## Section\nbody line\nmore body"
        chunks = indexer.chunk_markdown("doc.md", text)
        self.assertEqual(len(chunks), 2)
        heads = chunks[0]
        self.assertEqual(heads["start_line"], 1)
        self.assertIn("# Title", heads["text"])
        section = chunks[1]
        self.assertEqual(section["start_line"], 4)  # "## Section" is line 4
        self.assertIn("## Section", section["text"])

    def test_python_splits_at_top_level_objects(self):
        text = "import os\n\ndef a():\n    return 1\n\ndef b():\n    return 2\n"
        chunks = indexer.chunk_python("m.py", text)
        # header (imports) + two functions
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[1]["start_line"], 3)  # def a() at line 3
        self.assertIn("def a", chunks[1]["text"])

    def test_decorators_stay_glued_to_their_function(self):
        text = "@dec\ndef a():\n    return 1\n"
        chunks = indexer.chunk_python("m.py", text)
        self.assertEqual(len(chunks), 1)
        self.assertIn("@dec", chunks[0]["text"])
        self.assertIn("def a", chunks[0]["text"])

    def test_line_numbers_resolve_into_the_real_file(self):
        # every chunk's span must be a valid slice of the source
        text = "line 1\n" * 200
        chunks = indexer.chunk_markdown("big.md", text)
        for c in chunks:
            self.assertGreaterEqual(c["start_line"], 1)
            self.assertGreaterEqual(c["end_line"], c["start_line"])


class TestPromptAssembly(unittest.TestCase):
    def test_format_context_numbers_from_start_offset(self):
        block = format_context("f.py", "alpha\nbeta", start=41)
        self.assertIn("41| alpha", block)
        self.assertIn("42| beta", block)
        self.assertIn('path="f.py"', block)

    def test_build_messages_shape(self):
        msgs = build_messages("What is X?", [format_context("f.md", "X is Y")])
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn(DECLINE_PHRASE, msgs[0]["content"])
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("What is X?", msgs[-1]["content"])


class TestOfflineCLI(unittest.TestCase):
    def test_mock_ask_answers_and_exits_zero(self):
        os.environ["PROVIDER"] = "mock"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["ask", "hello there"])
        self.assertEqual(code, 0)
        self.assertIn("[mock]", out.getvalue())
        self.assertIn("hello there", out.getvalue())
        self.assertIn("cost:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
