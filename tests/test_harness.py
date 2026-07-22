"""Tests for the agent harness (feat/harness): offline, no key, no network.

The harness is pure boundary logic, so it tests cleanly against a temp corpus
and a FakeProvider that scripts tool calls. Two things matter: the sandbox
refuses what v05's inline jail let through (a dotfile, a non-source suffix, a
path escape), and the loop keeps running on a refusal instead of crashing.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo import agent  # noqa: E402
from askrepo.harness import (  # noqa: E402
    ALLOW, ASK, DENY, AuditLog, Harness, PermissionPolicy, ReadOnlySandbox,
    SandboxError, default_harness, permissive_harness,
)


class TestPermissionPolicy(unittest.TestCase):
    def test_default_deny_closes_the_tool_list(self):
        p = PermissionPolicy(default=DENY).allow("grep")
        self.assertEqual(p.decide("grep"), ALLOW)
        self.assertEqual(p.decide("rm"), DENY)  # unlisted = denied

    def test_default_harness_allows_only_the_three_read_tools(self):
        h = default_harness("/tmp")
        for t in ("grep", "read_file", "list_dir"):
            self.assertEqual(h.policy.decide(t), ALLOW)
        self.assertEqual(h.policy.decide("write_file"), DENY)


class TestReadOnlySandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "ok.md"), "w") as f:
            f.write("# fine\nreadable source")
        with open(os.path.join(self.tmp, ".env"), "w") as f:
            f.write("SECRET=nope")
        with open(os.path.join(self.tmp, "data.bin"), "w") as f:
            f.write("binary-ish")

    def test_reads_an_allowlisted_source_file(self):
        sb = ReadOnlySandbox(self.tmp)
        self.assertIn("readable source", sb.read_text("ok.md"))

    def test_refuses_a_dotfile(self):
        sb = ReadOnlySandbox(self.tmp)
        with self.assertRaises(SandboxError):
            sb.read_text(".env")  # the planted-secret case, refused by rule

    def test_refuses_a_non_source_suffix(self):
        sb = ReadOnlySandbox(self.tmp)
        with self.assertRaises(SandboxError):
            sb.read_text("data.bin")

    def test_refuses_a_path_escape(self):
        sb = ReadOnlySandbox(self.tmp)
        with self.assertRaises(SandboxError):
            sb.resolve("../../etc/passwd")

    def test_missing_file_is_an_ordinary_error_not_a_refusal(self):
        # a typo'd path must NOT masquerade as a security denial in the audit
        sb = ReadOnlySandbox(self.tmp)
        with self.assertRaises(OSError) as ctx:
            sb.read_text("nope.md")
        # OSError, specifically NOT SandboxError (which means "refused by rule")
        self.assertNotIsInstance(ctx.exception, SandboxError)

    def test_no_write_method_exists(self):
        # the read-only sandbox literally cannot be told to write
        self.assertFalse(hasattr(ReadOnlySandbox(self.tmp), "write"))

    def test_permissive_sandbox_reads_anything_in_the_jail(self):
        sb = permissive_harness(self.tmp).sandbox
        self.assertIn("SECRET", sb.read_text(".env"))  # the v05 before-picture
        with self.assertRaises(SandboxError):
            sb.resolve("../escape")  # even permissive keeps the jail


class TestHarnessDecide(unittest.TestCase):
    def test_ask_without_approver_denies(self):
        h = Harness(PermissionPolicy(default=ALLOW).ask("grep"),
                    ReadOnlySandbox(tempfile.mkdtemp()))
        self.assertEqual(h.decide("grep", {}), DENY)  # fail closed, unattended

    def test_ask_with_approver_allows(self):
        h = Harness(PermissionPolicy(default=ALLOW).ask("grep"),
                    ReadOnlySandbox(tempfile.mkdtemp()),
                    approver=lambda tool, args: True)
        self.assertEqual(h.decide("grep", {}), ALLOW)

    def test_every_call_is_audited(self):
        h = default_harness(tempfile.mkdtemp())
        h.decide("grep", {"pattern": "x"})
        h.decide("write_file", {"path": "y"})
        self.assertEqual(len(h.audit.entries), 2)
        self.assertEqual(len(h.audit.denied), 1)  # the write


class FakeProvider:
    """Scripts a fixed sequence of tool-call turns, then a text answer."""

    name = "fake"
    model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.usage = (0, 0)
        self.i = 0

    def step(self, messages, tools):
        if self.i < len(self.script):
            name, args = self.script[self.i]
            self.i += 1
            return {"kind": "tools",
                    "calls": [{"id": f"c{self.i}", "name": name, "args": args}],
                    "assistant": {"role": "assistant", "content": None}}
        return {"kind": "text", "text": "done"}

    def tool_results_messages(self, results):
        # remember the last tool output so a test can assert on the refusal
        self.last_output = results[-1][1]
        return [{"role": "user", "content": str(results)}]


class TestAgentUnderHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "readme.md"), "w") as f:
            f.write("# hi\nreal content")
        with open(os.path.join(self.tmp, ".env"), "w") as f:
            f.write("KEY=leak-me")

    def test_default_harness_refuses_dotfile_read_in_band(self):
        prov = FakeProvider([("read_file", {"path": ".env"})])
        text, touched, n, cost = agent.answer("q", self.tmp, prov)  # default harness
        self.assertIn("error", prov.last_output.lower())  # loop got a refusal...
        self.assertNotIn("leak-me", prov.last_output)      # ...not the secret
        self.assertEqual(text, "done")                     # ...and kept going

    def test_denied_tool_never_reaches_the_sandbox(self):
        prov = FakeProvider([("delete_everything", {})])
        agent.answer("q", self.tmp, prov)
        self.assertIn("denied by permission policy", prov.last_output)

    def test_permissive_harness_would_leak_the_secret(self):
        # the before-picture: prove the attack is real when the boundary is off
        prov = FakeProvider([("read_file", {"path": ".env"})])
        agent.answer("q", self.tmp, prov, harness=permissive_harness(self.tmp))
        self.assertIn("leak-me", prov.last_output)


if __name__ == "__main__":
    unittest.main()
