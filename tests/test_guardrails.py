"""Tests for the injection guardrails: pure functions, offline."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo import guardrails  # noqa: E402


class TestExfilDetection(unittest.TestCase):
    def test_flags_markdown_image_to_any_domain(self):
        # a Q&A answer never needs an embedded image; flag it regardless
        text = "See ![status](https://collect.attacker.example/ping.png) here."
        self.assertTrue(guardrails.find_exfil_links(text))

    def test_flags_link_to_unallowlisted_domain(self):
        text = "Recover at [account portal](http://nimbus-support.help)."
        self.assertTrue(guardrails.find_exfil_links(text))

    def test_allows_link_to_allowlisted_domain(self):
        text = "See the docs at [guide](https://platform.claude.com/docs)."
        self.assertEqual(guardrails.find_exfil_links(text), [])

    def test_plain_text_and_citations_untouched(self):
        text = "The limit is 10 (db.py:4)."
        clean, flagged = guardrails.sanitize(text)
        self.assertEqual(clean, text)
        self.assertEqual(flagged, [])

    def test_sanitize_removes_the_channel(self):
        text = "Answer. ![x](https://collect.attacker.example/p.png)"
        clean, flagged = guardrails.sanitize(text)
        self.assertNotIn("collect.attacker.example", clean)
        self.assertIn("removed", clean)
        self.assertEqual(len(flagged), 1)


class TestHardening(unittest.TestCase):
    def test_harden_system_appends_notice(self):
        hardened = guardrails.harden_system("Base prompt.")
        self.assertTrue(hardened.startswith("Base prompt."))
        self.assertIn("UNTRUSTED DATA", hardened)

    def test_harden_messages_only_touches_system(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        out = guardrails.harden_messages(msgs)
        self.assertIn("UNTRUSTED DATA", out[0]["content"])
        self.assertEqual(out[1]["content"], "hi")
        self.assertEqual(msgs[0]["content"], "sys")  # original not mutated


if __name__ == "__main__":
    unittest.main()
