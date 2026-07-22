"""Multi-turn grounded chat: one turn = retrieve, assemble, answer, remember.

`ask` is one question in isolation. `chat` holds a conversation, and that turns
the window into a contested resource with three claimants every turn:

  1. the system contract (v02) + a running summary of compacted old turns
  2. the retrieved chunks, but now *accumulating* across turns (assemble.py)
  3. the recent conversation turns, verbatim (memory.py)

This module budgets all three explicitly and runs one turn. Two memories with
two lifetimes do the work: a ChatMemory carries the conversation thread (clean
question/answer text, compacted when it outgrows its slice), and a ChunkPool
carries the evidence (retrieved chunks, aged so fresh relevance wins the budget
fight). The chunk context is attached to the *current* outgoing message only 
never persisted into the thread, so compaction never folds raw file text into
a summary, and the conversation stays legible.

Offline: on the mock provider there's no index, so a turn just converses (the
compaction path still runs, on the deterministic summarizer): the v00 promise,
kept into the chat feature.
"""

import sys
from dataclasses import dataclass, field

from askrepo.assemble import ChunkPool, assemble
from askrepo.memory import ChatMemory, truncating_summarizer
from askrepo.prompts import FEW_SHOTS, SYSTEM_PROMPT
from askrepo.providers import cost_usd


def model_summarizer(provider):
    """A summarizer backed by the chat provider, used when it isn't the mock.

    Compaction is itself a model call; framing it as "summarize these turns,
    preserve concrete facts and file references" keeps citations recoverable in
    later turns. Falls back to the offline summarizer if the call yields nothing.
    """
    def summarize(messages):
        transcript = "\n".join(f"{m['role']}: {m.get('content','')}" for m in messages)
        prompt = [
            {"role": "system", "content":
                "Summarize the conversation below in a few sentences. Preserve "
                "concrete facts, decisions, and any file paths or (path:line) "
                "citations mentioned. Be terse."},
            {"role": "user", "content": transcript},
        ]
        out = "".join(provider.complete(prompt)).strip()
        return out or truncating_summarizer(messages)
    return summarize


@dataclass
class ChatSession:
    """All the state a conversation carries between turns."""

    memory: ChatMemory
    pool: ChunkPool = field(default_factory=ChunkPool)
    chunk_budget: int = 1500
    k: int = 5
    blend: float = 0.7
    turn: int = 0
    last_context: object = None  # the Assembled result of the most recent turn


def new_session(window_tokens, provider):
    """Split the window into a chunk slice and a conversation slice.

    A fixed, visible split (half evidence, a third conversation, the rest for
    the system contract and the answer) so the budgeting is legible rather than
    magic. The conversation slice is ChatMemory's compaction budget.
    """
    chunk_budget = window_tokens // 2
    turn_budget = window_tokens // 3
    summarizer = truncating_summarizer if provider.name == "mock" else model_summarizer(provider)
    memory = ChatMemory(budget_tokens=turn_budget, keep_recent=2, summarizer=summarizer)
    return ChatSession(memory=memory, chunk_budget=chunk_budget)


def _retrieve(question, session):
    """This turn's (score, chunk) pairs, or [] on the mock/no-index path."""
    from askrepo.retrieve import load_index, retrieve

    index = load_index()
    return retrieve(question, index, k=session.k, blend=session.blend)


def respond(session, question, provider, *, on_context=None):
    """Run one turn and return (answer_text, cost_usd).

    Steps: retrieve fresh chunks -> age them into the pool -> assemble the pool
    under the chunk budget (deciding what survives) -> build system (contract +
    summary) + prior turns + the current question with its surviving context ->
    generate -> persist the clean turn (may compact).
    """
    session.turn += 1

    # 1. retrieval (skipped on the mock: no index, no key)
    context_text = ""
    if provider.name != "mock":
        session.pool.add(_retrieve(question, session), session.turn)
        assembled = assemble(session.pool.sections(session.turn), session.chunk_budget)
        session.last_context = assembled
        if assembled.kept:
            context_text = "Context (retrieved across this conversation):\n\n" + assembled.text() + "\n\n"
        if on_context:
            on_context(assembled)

    # 2. system = contract + running summary; prior turns carry the thread
    summary, prior_turns = session.memory.build()
    system = SYSTEM_PROMPT
    if summary:
        system += f"\n\nEarlier in this conversation:\n{summary}"

    user_content = f"{context_text}Question: {question}"
    messages = [{"role": "system", "content": system}, *FEW_SHOTS, *prior_turns,
                {"role": "user", "content": user_content}]

    # 3. generate
    answer = "".join(provider.complete(messages))
    cost = cost_usd(provider) or 0.0

    # 4. persist the CLEAN turn (question/answer text only, never the chunk
    #    context), so compaction summarizes conversation, not file dumps
    session.memory.add("user", question)
    session.memory.add("assistant", answer)
    return answer, cost


def context_line(session):
    """One-line accounting of what this turn's window held, for --show-context."""
    mem = session.memory.info()
    kept = len(session.last_context.kept) if session.last_context else 0
    dropped = len(session.last_context.dropped) if session.last_context else 0
    used = session.last_context.tokens_used if session.last_context else 0
    return (f"[context: {kept} chunks kept / {dropped} evicted ({used} tok) · "
            f"{mem['turns_sent']} turns sent · {mem['compactions']} compactions · "
            f"summary {'yes' if mem['has_summary'] else 'no'}]")
