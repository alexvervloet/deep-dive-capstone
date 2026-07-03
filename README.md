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
secrun python -m askrepo ask "hello"   # same question, real model, real cost line
```

## The step log

Each step is a tag; `git checkout <tag>` shows the project as it stood then.
`main` is always the latest. Fuller definitions in
[../CAPSTONE.md](../CAPSTONE.md).

| Tag | Dive exercised | Status | What it added |
|-----|----------------|--------|---------------|
| `v00-scaffold` | — (house style) | **done** | CLI skeleton, mock provider, `check_setup.py`; runs offline |
| `v01-chat` | OpenAI + Claude API | **done** | real streamed answers from either provider, priced from real token usage |
| `v02-prompt` | Prompt Engineering | **done** | citation contract: grounded answers with (path:line), declines the rest |
| `v03-rag` | RAG | next | index the series, ask, get cited answers |
| `v04-evals` | Evals | — | golden set, runner, frozen baseline + corpus manifest |
| `v05-agent` | Agents | — | tool-loop retrieval; measured RAG-vs-agent verdict |
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
