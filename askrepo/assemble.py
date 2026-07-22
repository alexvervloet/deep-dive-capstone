"""Pack the window deliberately: which sections, and which retrieved chunks
survive under a token budget.

A single `ask` retrieves k chunks and sends them once. A `chat` is different:
every turn retrieves *fresh* chunks, and they accumulate. Send them all and the
window overflows within a few turns; keep only the newest and you forget the
file the user asked about two turns ago and is still discussing. So chat needs
a policy for **which retrieved chunks survive across turns**: the askrepo-
specific problem this module exists for.

Two pieces:

  assemble()   greedily keep the highest-priority sections that fit a budget
               (the generic discipline, from the context dive). A big low-
               priority section can't starve several small high-priority ones.
  ChunkPool    the chat-specific carrier: retrieved chunks from every turn,
               deduped, each scored by *retrieval strength decayed by age*.
               Turn it into Sections and hand them to assemble(); what fits
               survives, what doesn't is evicted, and the caller can *see*
               which, so context is never a black box (the v03 promise).

Both are pure functions over data: no model, no key, testable offline.
Adapted from context-engineering-deep-dive/context/assemble.py.
"""

from dataclasses import dataclass, field

from askrepo import tokens


@dataclass
class Section:
    """One piece of candidate context. Higher `priority` = keep it first."""

    label: str
    text: str
    priority: float
    kind: str = "section"  # "chunk" sections carry a citable source


@dataclass
class Assembled:
    kept: list
    dropped: list
    tokens_used: int
    budget: int

    def text(self):
        return "\n\n".join(s.text for s in self.kept)


def assemble(sections, budget_tokens):
    """Keep the highest-priority sections that fit `budget_tokens`.

    Ties keep the earlier-listed section. A section that doesn't fit is dropped
    and we keep trying smaller lower-priority ones, so one giant low-priority
    block can't starve several small high-priority ones.
    """
    ordered = sorted(enumerate(sections), key=lambda p: (-p[1].priority, p[0]))
    kept, dropped, used = [], [], 0
    for _, section in ordered:
        cost = tokens.estimate(section.text)
        if used + cost <= budget_tokens:
            kept.append(section)
            used += cost
        else:
            dropped.append(section)
    return Assembled(kept=kept, dropped=dropped, tokens_used=used, budget=budget_tokens)


class ChunkPool:
    """Retrieved chunks carried across chat turns, aged so fresh relevance wins.

    Each turn's retrieval is folded in with `add()`. A chunk seen in several
    turns keeps its best score and its most-recent sighting. `sections()` scores
    every chunk as `score * decay**(turns_since_seen)`: a chunk the user keeps
    circling stays high; one mentioned once and abandoned fades and is evicted
    the first turn the budget is tight. That decay is the survival policy.
    """

    def __init__(self, decay=0.6, max_pool=40):
        self.decay = decay
        self.max_pool = max_pool
        self._chunks = {}  # key -> {chunk, score, last_turn}

    @staticmethod
    def _key(chunk):
        return f"{chunk['path']}:{chunk['start_line']}"

    def add(self, scored_chunks, turn):
        """Merge one turn's (score, chunk) pairs into the pool."""
        for score, chunk in scored_chunks:
            key = self._key(chunk)
            existing = self._chunks.get(key)
            if existing is None:
                self._chunks[key] = {"chunk": chunk, "score": score, "last_turn": turn}
            else:  # seen before: keep best score, refresh recency
                existing["score"] = max(existing["score"], score)
                existing["last_turn"] = turn
        # bound the pool: drop the coldest if it grew past the cap
        if len(self._chunks) > self.max_pool:
            for key in sorted(
                self._chunks,
                key=lambda k: (self._chunks[k]["last_turn"], self._chunks[k]["score"]),
            )[: len(self._chunks) - self.max_pool]:
                del self._chunks[key]

    def sections(self, current_turn):
        """Every pooled chunk as a citation-labeled Section, aged by recency."""
        from askrepo.prompts import format_context

        out = []
        for entry in self._chunks.values():
            chunk = entry["chunk"]
            age = current_turn - entry["last_turn"]
            priority = entry["score"] * (self.decay ** age)
            out.append(Section(
                label=self._key(chunk),
                text=format_context(chunk["path"], chunk["text"], start=chunk["start_line"]),
                priority=priority,
                kind="chunk",
            ))
        return out
