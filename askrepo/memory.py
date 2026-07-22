"""Conversation memory under a budget: keep recent turns, compact the rest.

The API is stateless: "memory" is just the message list you choose to resend
each turn. You can't resend everything forever, so memory is a *policy*: what
to keep verbatim and what to fold away. askrepo chat uses compaction (the
context dive's SummaryMemory): the most recent `keep_recent` turns stay word-
for-word; everything older collapses into a running summary carried in the
system prompt. Bounded like a sliding window, but old *facts* survive because
they ride in the summary, not the raw turns.

The summarizer is injectable. The default is extractive and offline (no model,
no key) so the mock path and the test suite compact for real; the chat CLI
swaps in a model-backed summarizer when a real provider is configured. Adapted
from context-engineering-deep-dive/context/memory.py.
"""

from askrepo import tokens


def truncating_summarizer(messages, limit=400):
    """A deterministic, offline summary: the older turns, condensed by hand.

    Not a smart summary: it keeps the leading text of each folded turn so a
    fact stated early ("index with k=8") survives compaction without a model.
    The chat CLI replaces this with a model summarizer when one is available;
    this is what keeps compaction testable with no key."""
    lines = []
    for m in messages:
        content = " ".join(m.get("content", "").split())
        if content:
            lines.append(f"{m['role']}: {content}")
    joined = " | ".join(lines)
    return joined if len(joined) <= limit else joined[: limit - 1] + "..."


class ChatMemory:
    """Turns plus a running summary, compacted when the turns exceed a budget.

    `add()` records a turn and may trigger compaction; `build()` returns
    (summary, recent_turns): the summary goes in the system prompt, the turns
    go in the message list. `budget_tokens` bounds the *turns* only; the chat
    CLI sizes it as the window minus the system prompt and the retrieved-chunk
    budget, so the three claimants on the window are budgeted explicitly.
    """

    def __init__(self, budget_tokens, keep_recent=2, summarizer=truncating_summarizer):
        self.budget = budget_tokens
        self.keep_recent = keep_recent
        self.summarizer = summarizer
        self.turns = []
        self.summary = ""
        self.compactions = 0

    def add(self, role, content):
        self.turns.append({"role": role, "content": content})
        self._maybe_compact()

    def build(self):
        """Return (summary, recent_turns) for this turn's request."""
        return self.summary, list(self.turns)

    def _turn_tokens(self):
        # summary rides in the system prompt, so it counts against the budget too
        return tokens.estimate(self.summary) + tokens.estimate_messages(self.turns)

    def _maybe_compact(self):
        # keep peeling the oldest turns into the summary until the turns fit 
        # one summarize call can still leave us over budget, so loop
        while self._turn_tokens() > self.budget and len(self.turns) > self.keep_recent:
            old = self.turns[: -self.keep_recent]
            self.turns = self.turns[-self.keep_recent :]
            to_summarize = []
            if self.summary:
                to_summarize.append({"role": "system", "content": self.summary})
            to_summarize.extend(old)
            self.summary = self.summarizer(to_summarize)
            self.compactions += 1

    def info(self):
        return {
            "turns_sent": len(self.turns),
            "compactions": self.compactions,
            "has_summary": bool(self.summary),
            "turn_tokens": self._turn_tokens(),
            "budget": self.budget,
        }
