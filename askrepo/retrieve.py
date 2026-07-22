"""Hybrid retrieval over the saved index: vector search + BM25, blended.

Vector search matches on *meaning*; BM25 matches on the actual *words* 
which is what you want for the things embeddings are worst at: module names,
flags, error strings, `secrun`. (Both implementations are adapted from
rag-deep-dive/rag/, where store.py and keyword.py teach them from scratch.)

The blend weight is a knob, not a truth. The RAG dive's own hybrid example
(ex07) found a 50/50 blend ranking the right chunk *worse* than vector-only
on some queries, so BLEND is configurable here and *measured* at v04, not
asserted. Default 0.7 (vector-leaning) until the numbers say otherwise.

One hard rule: the query is embedded with the model the index was built
with: vectors from different models live in different spaces. The stack is
read out of the index, not out of PROVIDER, so you can chat with Claude over
an OpenAI-embedded index without lying to the math.
"""

import json
import math
import os
import re
from collections import Counter

from askrepo.indexer import INDEX_PATH
from askrepo.providers import embed


def load_index():
    if not os.path.exists(INDEX_PATH):
        raise SystemExit(
            "No index found. Build one first:\n"
            "    secrun python -m askrepo index <corpus-path>"
        )
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def tokenize(text):
    """Lowercase word/number tokens, keeping hyphenated names like
    "rag-deep-dive" whole; exact-match tokens are BM25's whole point."""
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())


class BM25:
    """BM25 keyword scoring over the chunk texts (see rag/keyword.py for the
    from-scratch walkthrough of k1, b, and IDF)."""

    def __init__(self, texts, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        docs = [tokenize(t) for t in texts]
        self.doc_len = [len(d) for d in docs]
        self.avgdl = sum(self.doc_len) / len(docs) if docs else 0.0
        self.tf = [Counter(d) for d in docs]
        df = Counter()
        for doc in docs:
            df.update(set(doc))
        n = len(docs)
        self.idf = {
            word: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
            for word, freq in df.items()
        }

    def scores(self, query):
        q_terms = tokenize(query)
        out = []
        for i, counts in enumerate(self.tf):
            norm = (
                self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                if self.avgdl
                else self.k1
            )
            score = 0.0
            for term in q_terms:
                freq = counts.get(term, 0)
                if freq:
                    score += self.idf.get(term, 0.0) * freq * (self.k1 + 1) / (freq + norm)
            out.append(score)
        return out


def _normalize(scores):
    """Min-max to [0, 1] so two differently-scaled scores can be blended."""
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [0.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def retrieve(question, index, k=5, blend=0.7):
    """Top-k chunks for the question: blend * vector + (1 - blend) * BM25.

    Returns a list of (score, chunk) pairs, best first.
    """
    chunks = index["chunks"]
    # embedding is one clean request: the ideal thing to retry on a blip
    from askrepo.ops import with_retry

    query_vector, _ = with_retry(
        lambda: embed([question], index["stack"], input_type="query")
    )
    vector_scores = [cosine_similarity(query_vector[0], c["vector"]) for c in chunks]
    keyword_scores = BM25([c["text"] for c in chunks]).scores(question)

    v_norm = _normalize(vector_scores)
    kw_norm = _normalize(keyword_scores)
    blended = [blend * v + (1 - blend) * kw for v, kw in zip(v_norm, kw_norm)]

    ranked = sorted(zip(blended, chunks), key=lambda p: p[0], reverse=True)
    return ranked[:k]
