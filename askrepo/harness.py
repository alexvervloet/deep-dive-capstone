"""The boundary around agent mode's tools: policy + sandbox + audit.

v06's verdict was that the agent's file tools are the attack surface — the
injection rides in on `read_file` — and its defenses were *advisory*: a
system-prompt notice and an output check. The model can be talked out of
advice; that's what task-aligned injection is. This module is the structural
fix (adapted from agent-harness-deep-dive/harness/policy.py + sandbox.py):
rules enforced in code the model never sees and cannot argue with. The model
proposes, the sandbox disposes.

Three pieces, each tiny on purpose:

  PermissionPolicy   which tools may run at all — declarative, deny-unlisted,
                     readable and diffable on its own (the same idea as
                     Claude Code's permission modes).
  ReadOnlySandbox    where file tools may look. v05 already jailed paths to
                     the corpus root; the harness lifts that inline check out
                     and closes what it missed: `read_file` would open ANY
                     file inside the jail — a planted `.env`, a key file —
                     while grep only ever walked .md/.py. Now reads are
                     allowlisted by suffix and dotfiles are refused, jail or
                     no jail. (There is deliberately no write method — this
                     sandbox can't be talked into becoming a weapon.)
  AuditLog           what actually happened: every proposed call, its
                     verdict, and how it ended — the flight recorder the
                     red-team table reads from.

Honest scope: the harness stops tool *abuse* — reading what should never be
read, running what was never allowed. It cannot stop a plausible lie planted
in a file the agent is *supposed* to read (v06's fact-poison); no boundary
on tools fixes that, because reading the file was the legitimate job.
"""

import os
from dataclasses import dataclass, field

from askrepo.indexer import INDEXED_EXTENSIONS
from askrepo.ops import log

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# What read_file may open: the indexed types plus harmless text configs.
# An allowlist, not a blocklist — an attacker has infinite names for a secret
# file, but we can name the few suffixes a Q&A agent legitimately needs.
READABLE_SUFFIXES = INDEXED_EXTENSIONS | {".txt", ".toml", ".json"}


class SandboxError(RuntimeError):
    """A tool tried to act outside the boundary. Refused, not crashed."""


@dataclass
class PermissionPolicy:
    """Per-tool verdicts plus a default for anything unlisted.

    askrepo's default is DENY: a Q&A agent's tool list should be closed —
    a tool nobody granted is a tool that doesn't run. ASK exists for tools
    that need a human (none do today; see Harness.decide for how an ASK
    resolves when no approver is present).
    """

    default: str = DENY
    rules: dict = field(default_factory=dict)

    def decide(self, tool_name):
        return self.rules.get(tool_name, self.default)

    def allow(self, *names):
        for n in names:
            self.rules[n] = ALLOW
        return self

    def ask(self, *names):
        for n in names:
            self.rules[n] = ASK
        return self

    def deny(self, *names):
        for n in names:
            self.rules[n] = DENY
        return self


class ReadOnlySandbox:
    """A path jail plus read rules. Every check is on the *canonical* path —
    realpath collapses `..` and follows symlinks, so the tricks a raw-string
    startswith would miss are the first thing caught."""

    def __init__(self, root, readable_suffixes=READABLE_SUFFIXES,
                 allow_dotfiles=False):
        self.root = os.path.realpath(root)
        self.readable_suffixes = readable_suffixes  # None = any suffix
        self.allow_dotfiles = allow_dotfiles

    def resolve(self, path):
        """Resolve a model-supplied path inside the jail, or refuse."""
        candidate = os.path.realpath(os.path.join(self.root, path or "."))
        if candidate != self.root and not candidate.startswith(self.root + os.sep):
            raise SandboxError(f"path {path!r} escapes the corpus. Refused.")
        return candidate

    def read_text(self, path):
        """Read a file the rules permit: inside the jail, an allowlisted
        suffix, not a dotfile. The refusals name the rule, so the model (and
        the audit log) can see *why* — a silent empty string would just make
        it try harder."""
        real = self.resolve(path)
        rel = os.path.relpath(real, self.root)
        if not self.allow_dotfiles and any(
            part.startswith(".") for part in rel.split(os.sep)
        ):
            raise SandboxError(f"{path!r} is a dotfile. Refused by sandbox policy.")
        suffix = os.path.splitext(real)[1]
        if self.readable_suffixes is not None and suffix not in self.readable_suffixes:
            raise SandboxError(
                f"{path!r} is not a readable type "
                f"({', '.join(sorted(self.readable_suffixes))}). Refused."
            )
        # A missing file is an ordinary tool error, NOT a policy refusal —
        # raise a plain OSError so the audit log doesn't count a typo as a
        # security denial. SandboxError is reserved for "the rules said no."
        if not os.path.isfile(real):
            raise FileNotFoundError(f"no such file: {path!r}")
        with open(real, encoding="utf-8") as f:
            return f.read()


class AuditLog:
    """Every proposed tool call and what became of it, in order."""

    def __init__(self):
        self.entries = []

    def record(self, tool, args, verdict, note=""):
        entry = {"tool": tool, "args": dict(args), "verdict": verdict, "note": note}
        self.entries.append(entry)
        log("info", "tool.audit", **entry)  # rides v07's structured trace

    @property
    def denied(self):
        return [e for e in self.entries if e["verdict"] != ALLOW]


@dataclass
class Harness:
    """The bundle agent.py consults on every call: policy, sandbox, audit.

    `approver(tool, args) -> bool` resolves ASK verdicts. Without one, ASK
    means DENY — an unattended session must fail closed, not wave things
    through because nobody was there to say no.
    """

    policy: PermissionPolicy
    sandbox: ReadOnlySandbox
    audit: AuditLog = field(default_factory=AuditLog)
    approver: object = None

    def decide(self, tool, args):
        verdict = self.policy.decide(tool)
        if verdict == ASK:
            approved = bool(self.approver and self.approver(tool, args))
            verdict = ALLOW if approved else DENY
            self.audit.record(tool, args, verdict,
                              note="ask:" + ("approved" if approved else "no approver"))
            return verdict
        self.audit.record(tool, args, verdict)
        return verdict


def default_harness(corpus_root):
    """What agent mode runs under on main: the three read tools and nothing
    else; reads allowlisted by suffix, dotfiles refused."""
    policy = PermissionPolicy(default=DENY).allow("grep", "read_file", "list_dir")
    return Harness(policy=policy, sandbox=ReadOnlySandbox(corpus_root))


def permissive_harness(corpus_root):
    """The v05 before-picture, kept runnable so the red-team can measure the
    delta: any tool, any file — the jail is the only rule. This is not a
    mode you choose; it is the yardstick the default is compared against."""
    policy = PermissionPolicy(default=ALLOW)
    sandbox = ReadOnlySandbox(corpus_root, readable_suffixes=None,
                              allow_dotfiles=True)
    return Harness(policy=policy, sandbox=sandbox)
