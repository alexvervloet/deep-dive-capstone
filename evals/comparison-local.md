# Local vs cloud — the quality gap, measured (ext-local)

Same golden set (40 questions), same corpus, **same judge** (gpt-4o-mini) grading
every side so a correctness delta is the *answerer* moving, not the grader. Three
stacks, differing end to end — that's the point of `feat/local`:

- **cloud** — answers `gpt-4o-mini`, embeddings `text-embedding-3-small` (the
  committed v04 baseline, `2026-07-03`).
- **local (small)** — answers `qwen3:8b` (Q4_K_M, via Ollama on localhost),
  embeddings `nomic-embed-text`.
- **local (big, remote)** — answers `qwen3.6-35b-a3b`, embeddings
  `text-embedding-qwen3-embedding-0.6b`, both on a **separate LAN box** via LM
  Studio's OpenAI-compatible endpoint (the "runner of choice on another
  machine" case). Nothing leaves the network on the product path; only the
  eval's judge is cloud, and that's measurement infrastructure, not the system.

| metric | cloud (gpt-4o-mini) | local 8b (localhost) | local 35b (remote box) |
|---|---|---|---|
| judged correctness | 0.771 | **0.843** | 0.786 |
| retrieval hit@k | 0.886 | 0.886 | 0.829 |
| citation resolve | 0.953 | 0.781 | **1.000** |
| citation match | 0.721 | 0.500 | 0.512 |
| citations / answer | 1.2 | 0.9 | 1.2 |
| decline accuracy | 1.000 | 1.000 | 0.800 |
| mean cost / question | $0.000407 | **$0.000000** | **$0.000000** |
| mean latency | 2.7s | 12.2s | 36.7s |

| correctness by category | cloud | local 8b | local 35b |
|---|---|---|---|
| code | 0.562 | **0.750** | **0.750** |
| concept | 0.900 | **1.000** | 0.850 |
| cross-dive | 0.600 | 0.600 | 0.500 |
| locator | 0.875 | 0.875 | 0.875 |

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

## The remote 35B, on a separate box — bigger isn't the point

Line 3 of §"§4 note" said it: *"a stronger or larger local model would likely
tighten the citation gap; measure it before believing it."* So we did — a 35B
model on a LAN box via LM Studio. The naive expectation "bigger local model →
clearly better" **did not hold**, and the reasons are the honest lesson:

1. **The 35B tied cloud; it didn't beat it.** 0.786 correctness is within judge
   noise (~±0.02) of the cloud baseline's 0.771 — a tie, not the +0.072 the
   *smaller* 8b posted. On this corpus, both local configs land in the same
   0.77–0.84 band as cloud. Model size wasn't the deciding variable; "local
   matches cloud on correctness" is the robust finding across all three.

2. **The weak link was the embedder, not the answerer.** The 35B is the only
   run whose retrieval hit@k dropped (0.829 vs 0.886 for both nomic and OpenAI).
   The single thing different on the retrieval side is the 0.6B qwen embedder —
   so the *small embedding model* fed the strong answerer slightly worse
   context. If you want to improve a local RAG stack, this says spend the
   upgrade on the embedder before the generator.

3. **Every citation was real — resolve hit a perfect 1.000** (better than both
   others), yet citation match stayed ~0.51: the same over-citing / grouping
   behavior, now with zero hallucinated references. Grounded, still loose on the
   exact `(path:line)` grammar.

4. **Its one decline miss is corpus contamination, not a broken contract.** The
   35B answered *"What is the capital of France?"* — with *"Paris
   (evals-deep-dive/examples/06_llm_judge.py:5)"*, because that toy question
   lives verbatim in the evals dive as example text. Retrieval found it and the
   "answer from context" contract did its job. The 8b only "passed" this one on
   retrieval luck (it didn't surface the example).

5. **Latency is the tax, and it scales.** 2.7s cloud → 12.2s local-8b → **36.7s
   local-35b**. The big model on a remote box is ~13.5× slower than cloud. For a
   35B thinking model reasoning before every answer, on one consumer GPU, that
   is the real, felt cost of the $0 privacy win — not dollars, wall-clock.

## The honest headline

On this corpus, **every local stack matched cloud on answer correctness for $0
with nothing leaving the network** — the smaller 8b even edged it. What you pay
is **speed** (4.5× to 13.5× slower, GPU pegged) and **citation-format fidelity**
(claims are grounded — the 35B's every citation resolves — but looser about the
exact per-claim `(path:line)` shape the v02 contract wants). And the
counter-intuitive one: a **bigger** local model didn't help here; a **better
embedder** would have, because retrieval — not generation — was the 35B's weak
spot.

Runs: cloud `2026-07-03T21:33:59` (`baseline.run.json`) · local-8b
`2026-07-04T15:56:17` · local-35b `2026-07-06T09:16:12` (`local-35b.run.json`);
all mode=rag, k=5, blend=0.7, judged by gpt-4o-mini; corpus manifests in the run
files. Reproduce the local runs:

```bash
# small, on this machine (Ollama)
ollama pull qwen3:8b && ollama pull nomic-embed-text
ASKREPO_INDEX=index/index.local.json PROVIDER=local python -m askrepo index ..
ASKREPO_INDEX=index/index.local.json PROVIDER=local LOCAL_MODEL=qwen3:8b \
  JUDGE_PROVIDER=openai JUDGE_MODEL=gpt-4o-mini python evals/run_evals.py

# big, on another box (any OpenAI-compatible runner — here LM Studio)
export LOCAL_BASE_URL=http://<box-ip>:1234/v1 LOCAL_MODEL=qwen3.6-35b-a3b \
  LOCAL_EMBED_MODEL=text-embedding-qwen3-embedding-0.6b
ASKREPO_INDEX=index/index.local-35b.json PROVIDER=local python -m askrepo index ..
ASKREPO_INDEX=index/index.local-35b.json PROVIDER=local \
  JUDGE_PROVIDER=openai JUDGE_MODEL=gpt-4o-mini python evals/run_evals.py
```

The judge stays `gpt-4o-mini` on every local run so it's measurement
infrastructure held constant, never the variable under test.
