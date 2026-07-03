# askrepo — the deep-dive capstone

Ask questions about a codebase in plain English, get answers **with `file:line`
citations**. This is the capstone of the AI Engineering deep-dive series: one
project built step by step, one deep dive per tag, whose default corpus is the
series itself — so the course answers questions about the course.

The roadmap — every step, what it builds, and its definition of done — lives in
[../CAPSTONE.md](../CAPSTONE.md). This README tracks what *exists*, tag by tag.

---

## Quickstart

The offline path always works — no key, no install:

```bash
python check_setup.py              # verifies your environment; makes no API call
python -m askrepo ask "hello"      # canned answer from the mock provider
```

For a real model, `pip install -r requirements.txt`, then `cp .env.example
.env` and set `PROVIDER=openai` or `PROVIDER=claude`. Your API key never goes
in `.env` — keychain + `secrun`, per [../SECRETS.md](../SECRETS.md):

```bash
secrun python -m askrepo index ..      # embed the series (~$0.01, once)
secrun python -m askrepo ask "which dive covers barge-in?"
```

The answer arrives with `(path:line)` citations that resolve to real files,
and stderr shows exactly which chunks were retrieved and what the call cost.
`ask --context <file>` skips retrieval and grounds by hand (the v02 path).

## The step log

Each step is a tag; `git checkout <tag>` shows the project as it stood then.
`main` is always the latest. Fuller definitions in
[../CAPSTONE.md](../CAPSTONE.md).

| Tag | Dive exercised | Status | What it added |
|-----|----------------|--------|---------------|
| `v00-scaffold` | — (house style) | **done** | CLI skeleton, mock provider, `check_setup.py`; runs offline |
| `v01-chat` | OpenAI + Claude API | **done** | real streamed answers from either provider, priced from real token usage |
| `v02-prompt` | Prompt Engineering | **done** | citation contract: grounded answers with (path:line), declines the rest |
| `v03-rag` | RAG | **done** | `index` + hybrid retrieval; `ask` grounds itself and cites real lines |
| `v04-evals` | Evals | **done** | 40-question golden set, 5-metric runner, frozen baseline + corpus manifest |
| `v05-agent` | Agents | next | tool-loop retrieval; measured RAG-vs-agent verdict |
| `v06-hardened` | Prompt Injection | — | red-team suite; attack success before/after defenses |
| `v07-production` | Production | — | caching, cost budget, retries, structured logs |

## What exists so far

**v00** proved the plumbing: `ask` sends your question through the full path
(CLI → provider → streamed answer) and the mock provider answers with a canned
response that *says it's canned*, so plumbing can't be mistaken for
intelligence. The interfaces the whole project grows on were in place from the
start:

- [`askrepo/providers.py`](askrepo/providers.py) — `complete(messages) -> stream`,
  the one contract every provider honors.
- [`askrepo/cli.py`](askrepo/cli.py) — subcommand skeleton that `index`, `chat`,
  `eval`, and `redteam` hang off later.
- [`askrepo/config.py`](askrepo/config.py) — defaults ← `.env` ← environment,
  which is what lets `secrun` inject keys per-command.

**v01** put real models in the mock's seat: OpenAI and Claude, both streamed,
behind the unchanged `complete()` interface — switching stacks is one env-var
change (`PROVIDER=openai|claude`, model overridable via `MODEL`). The cost
line is now real: each provider reports its actual token usage after the
stream ends, and the CLI prices it with the same numbers as
[../MODELS.md](../MODELS.md). The mock keeps working with nothing installed —
the SDKs import lazily, so the v00 promise holds at every tag.

**v02** taught it its job before giving it retrieval. The contract in
[`askrepo/prompts.py`](askrepo/prompts.py): answer only from provided
context, cite `(path:line)` for every claim, and reply "Not in this corpus."
(verbatim — later steps score it mechanically) when the context doesn't
cover the question. Context arrives by hand for now (`ask --context <file>`,
line-numbered so citations have something to point at) — the point is that
the contract is testable before the pipeline exists. `--raw` bypasses it to
show the before-picture: real transcripts of both, including the model
declining "What is the capital of France?" because grounding beats prior
knowledge, live in
[`evals/prompt_regression.md`](evals/prompt_regression.md) and become eval
seeds at v04. Side effect on the interface: the system prompt rides as a
`{"role": "system"}` message, and each provider translates it to its API's
shape (OpenAI: a message; Claude: the separate `system` parameter).

**v03** made the grounding automatic — the heart of the project.
[`indexer.py`](askrepo/indexer.py) walks a corpus and chunks it
*structure-aware and line-tracking* (markdown at headings, Python at
top-level `def`/`class`), so every chunk knows exactly where it lives and
citations resolve to real lines. [`retrieve.py`](askrepo/retrieve.py) blends
vector search with BM25 keyword scoring (both adapted from
[../rag-deep-dive/rag/](../rag-deep-dive/rag/)) — the blend weight is a
config knob (`BLEND`), not an assertion, because the RAG dive's own hybrid
example showed 50/50 losing to vector-only on some queries; v04 measures it.
[`answer.py`](askrepo/answer.py) glues retrieve → the v02 contract, and the
CLI prints every retrieved chunk so retrieval is never a black box. Indexing
the whole series: 380 files → 2,221 chunks, $0.0096. The query is always
embedded with the model the index was built with (recorded in the index) —
vectors from different models live in different spaces, so chat provider and
embedding stack are deliberately independent. Two notes for v04: models may
normalize cited paths (`../MODELS.md` → `MODELS.md`), and ../CAPSTONE.md is
*in* the corpus and names example eval questions — the golden set has to
account for both.

**v04** made quality a number. [`evals/golden.jsonl`](evals/golden.jsonl):
40 questions across locator / concept / code / cross-dive / negative
(adversarial arrives with v06's fixtures).
[`evals/run_evals.py`](evals/run_evals.py) scores five things per run —
retrieval hit@k, citation resolve (do cited lines exist?), citation match
(are they the expected files?), LLM-judged correctness against keypoints,
and decline accuracy — plus cost and latency, because quality numbers
without cost numbers are half a benchmark. Every run is stamped with a
**corpus manifest** (HEAD SHA of all 17 corpus repos plus this tool), which
is what makes the committed [`evals/baseline.run.json`](evals/baseline.run.json)
reproducible rather than nostalgic.

The frozen baseline, reported as measured (gpt-4o-mini, k=5, blend=0.7):
**hit@5 0.886 · citation resolve 0.953 · citation match 0.721 · judged
correctness 0.771 · decline accuracy 1.000 · $0.0004 and 2.7s per
question.** The story is in the category split: concept 0.90 and locator
0.88, but **code 0.56 and cross-dive 0.60** — chunk-level retrieval misses
specific functions even when it finds the right file (file-level hit@5
overstates chunk-level success), and paraphrased synthesis questions defeat
both vector and keyword matching. That gap is exactly what v05's agentic
retrieval gets to attack, with this baseline as the yardstick. Judge
verdicts were spot-checked by hand before freezing (the four zero-scores:
three honest pipeline failures kept as-is, one ambiguous golden question
fixed and the whole set rerun). Run-to-run judge noise is real (~±0.02 on
correctness) — trust deltas bigger than that, not smaller.
