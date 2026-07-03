# askrepo — the deep-dive capstone

Ask questions about a codebase in plain English, get answers **with `file:line`
citations**. This is the capstone of the AI Engineering deep-dive series: one
project built step by step, one deep dive per tag, whose default corpus is the
series itself — so the course answers questions about the course.

The roadmap — every step, what it builds, and its definition of done — lives in
[../CAPSTONE.md](../CAPSTONE.md). This README tracks what *exists*, tag by tag.

---

## Quickstart

Everything at the current tag runs **offline and free** — no key, no install:

```bash
python check_setup.py              # verifies your environment; makes no API call
python -m askrepo ask "hello"      # canned answer from the mock provider
```

To configure (optional at v00): `cp .env.example .env` and set `PROVIDER`.
Your API key never goes in `.env` — keychain + `secrun`, per
[../SECRETS.md](../SECRETS.md).

## The step log

Each step is a tag; `git checkout <tag>` shows the project as it stood then.
`main` is always the latest. Fuller definitions in
[../CAPSTONE.md](../CAPSTONE.md).

| Tag | Dive exercised | Status | What it added |
|-----|----------------|--------|---------------|
| `v00-scaffold` | — (house style) | **done** | CLI skeleton, mock provider, `check_setup.py`; runs offline |
| `v01-chat` | OpenAI + Claude API | next | real streamed answers from either provider |
| `v02-prompt` | Prompt Engineering | — | citation contract, declines off-topic asks |
| `v03-rag` | RAG | — | index the series, ask, get cited answers |
| `v04-evals` | Evals | — | golden set, runner, frozen baseline + corpus manifest |
| `v05-agent` | Agents | — | tool-loop retrieval; measured RAG-vs-agent verdict |
| `v06-hardened` | Prompt Injection | — | red-team suite; attack success before/after defenses |
| `v07-production` | Production | — | caching, cost budget, retries, structured logs |

## What v00 proves

Nothing intelligent — deliberately. `ask` sends your question through the full
path (CLI → provider → streamed answer) and the mock provider answers with a
canned response that *says it's canned*, so plumbing can't be mistaken for
intelligence. The interfaces the whole project grows on are already in place:

- [`askrepo/providers.py`](askrepo/providers.py) — `complete(messages) -> stream`,
  the one contract every provider (mock now; OpenAI/Claude at v01) honors.
- [`askrepo/cli.py`](askrepo/cli.py) — subcommand skeleton that `index`, `chat`,
  `eval`, and `redteam` hang off later.
- [`askrepo/config.py`](askrepo/config.py) — defaults ← `.env` ← environment,
  which is what lets `secrun` inject keys per-command from v01 on.
- Cost printed on every answer, starting now, while the honest number is $0.
