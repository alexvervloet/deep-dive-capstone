"""Token estimation: a budget you can do without a tokenizer.

Context engineering starts with arithmetic: *will this fit?* Every model has
a context window, and a multi-turn chat that keeps resending its history plus
freshly retrieved chunks is the thing that blows through it. Before deciding
what to keep (memory.py) or which chunks survive (assemble.py), you need a
rough token count.

We estimate at ~4 characters per token (English) plus a small per-message
overhead for the chat format's role markers. It's an approximation on purpose:
no key, no network, no tokenizer download; accurate enough to *reason about
budgets*, which is all context assembly needs. For the real bill, trust the
provider's `usage` field (v01 already prices from it); for deciding what goes
in the window, this is enough. Adapted from
context-engineering-deep-dive/context/tokens.py.
"""

_CHARS_PER_TOKEN = 4
_PER_MESSAGE_OVERHEAD = 4

# A few representative windows for sizing. Real numbers move (see ../MODELS.md);
# what matters is the ratio of your conversation to the window, not the figure.
CONTEXT_WINDOWS = {
    "gpt-4o-mini": 128_000,
    "claude-haiku-4-5": 200_000,
}


def estimate(text):
    """Tokens in a string (~4 chars/token, min 1 for non-empty text)."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_message(message):
    """One chat message's tokens, including the per-message format overhead."""
    return estimate(message.get("content", "")) + _PER_MESSAGE_OVERHEAD


def estimate_messages(messages):
    """A whole message list's tokens."""
    return sum(estimate_message(m) for m in messages)


def fits(messages, budget_tokens, system=""):
    """Does system + messages fit inside `budget_tokens`?"""
    return estimate(system) + estimate_messages(messages) <= budget_tokens
