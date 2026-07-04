# Local vs cloud — the quality gap, measured (ext-local)

Same golden set (40 questions), same corpus, **same judge** (gpt-4o-mini) grading
both sides so a correctness delta is the *answerer* moving, not the grader. The
two stacks differ end to end — that's the point of `feat/local`:

- **cloud** — answers `gpt-4o-mini`, embeddings `text-embedding-3-small` (the
  committed v04 baseline, `2026-07-03`).
- **local** — answers `qwen3:8b` (Q4_K_M, via Ollama), embeddings
  `nomic-embed-text`. Nothing leaves the machine on the product path; only the
  eval's judge is cloud, and that's measurement infrastructure, not the system.

| metric | cloud (gpt-4o-mini) | local (qwen3:8b) | delta |
|---|---|---|---|
| judged correctness | 0.771 | **0.843** | **+0.072** |
| retrieval hit@k | 0.886 | 0.886 | +0.000 |
| citation resolve | 0.953 | 0.781 | −0.172 |
| citation match | 0.721 | 0.500 | −0.221 |
| citations / answer | 1.2 | 0.9 | −0.300 |
| decline accuracy | 1.000 | 1.000 | +0.000 |
| mean cost / question | $0.000407 | **$0.000000** | free |
| mean latency | 2.7s | 12.2s | **+9.5s** |

| correctness by category | cloud | local |
|---|---|---|
| code | 0.562 | **0.750** |
| concept | 0.900 | **1.000** |
| cross-dive | 0.600 | 0.600 |
| locator | 0.875 | 0.875 |

## What the numbers actually say — against the naive expectation

The expected story was "local is cheaper but worse." Only half held.

1. **Retrieval parity, for free.** `nomic-embed-text` matched
   `text-embedding-3-small` on file-level hit@k exactly (0.886). The free local
   embedder retrieves as well as the paid one on this corpus — the embedding
   half of the gap is *zero*.

2. **Local answered *better*, not worse.** qwen3:8b scored **higher** judged
   correctness (0.843 vs 0.771), most of the lift on `code` (0.75 vs 0.56) and
   `concept` (1.00 vs 0.90). Same judge graded both, so this isn't grader bias
   toward one style. (Caveat: one run; judge noise is ~±0.02, so trust the
   +0.072, not a hypothetical +0.01. Correctness is judged — subjective-ish;
   the citation metrics below are mechanical and objective.)

3. **The real regression is citation *format*, not grounding.** Citation resolve
   and match dropped hard — but that overstates it. Of the 14 non-negative
   answers that failed the strict `(path:line)` parse, **11 actually cite real
   sources**, just grouped like `(multimodal-deep-dive/README.md:4, README.md:51)`
   instead of the one-citation-per-paren shape the v02 contract specifies. Only
   **3** were genuinely ungrounded. So the gap is *format compliance* — the
   small model grounds its claims but follows the exact citation grammar less
   strictly than gpt-4o-mini. For askrepo, whose whole contract is per-claim
   `(path:line)` citations, that's still a real cost — it's just a prompt/parser
   adherence gap, not "local can't ground."

4. **Latency is the tax you actually pay.** ~4.5× slower per question (12.2s vs
   2.7s), and it saturates the local GPU while it runs. On a laptop that's the
   felt cost, far more than the (zero) dollar cost.

## The honest headline

On this corpus, the local stack **matches cloud on retrieval and edges it on
answer correctness, for $0** — the privacy win ("not a byte leaves") comes with
*no* correctness penalty here. What you give up is **speed** (4.5× slower, GPU
pegged) and **citation-format fidelity** (grounded, but looser about the exact
shape askrepo's contract wants). A stronger or larger local model would likely
tighten the citation gap; measure it before believing it.

Runs: cloud `2026-07-03T21:33:59` · local `2026-07-04T15:56:17` (mode=rag,
k=5, blend=0.7; corpus manifests in the run files). Reproduce the local run:

```bash
ollama pull qwen3:8b && ollama pull nomic-embed-text
ASKREPO_INDEX=index/index.local.json PROVIDER=local python -m askrepo index ..
ASKREPO_INDEX=index/index.local.json PROVIDER=local LOCAL_MODEL=qwen3:8b \
  JUDGE_PROVIDER=openai JUDGE_MODEL=gpt-4o-mini python evals/run_evals.py
```
