# Prompt regression transcripts: v02-prompt

Real before/after runs showing what the prompt contract
([askrepo/prompts.py](../askrepo/prompts.py)) changes. "Before" is the bare
model (`--raw`); "after" is the contract. Every transcript below is a real
run from 2026-07-03 (gpt-4o-mini / claude-haiku-4-5), reproducible with the
commands shown. These become eval seeds at v04; the categories are marked.

The one-line summary: **the bare model's failure mode isn't wrongness, it's
confident helpfulness.** The contract converts "plausible essay" into either
a cited answer or an honest decline.

---

## 1. Ungrounded question: before/after (v04 category: `negative`)

**Ask:** `What chunk size should I use for RAG?` (no `--context`)

**Before** (`--raw`):

> The optimal chunk size for Retrieval-Augmented Generation (RAG) depends on
> several factors, including the nature of your data, the retrieval system
> you're using, and the specific task you're addressing. Here are some
> guidelines to help you determine an appropriate chunk size:
> 1. **Nature of the Data**: — For documents with dense information (like
> technical manuals or research papers), smaller chunks (100-300 tokens) may
> work better... *(continues for several paragraphs, zero sources)*

**After** (contract):

> Not in this corpus. (Guess: the acceptable chunk size for RAG might be
> defined in the project's documentation or a related README file.)

The before-answer isn't even wrong; that's what makes it dangerous. A user
can't tell generic training-data advice from what *this repo* teaches
(rag-deep-dive's ex05 has actual chunk-size measurements). The contract
refuses to blur that line.

## 2. Grounded question with citation (v04 category: `concept`)

**Ask:** `What model does the series default to, and why?`
`--context ../MODELS.md`

**After** (gpt-4o-mini):

> The series defaults to the model `gpt-4o-mini` because it is cheap, fast,
> multimodal, and great for learning and most tasks (MODELS.md:40).

Verified: MODELS.md line 40 is exactly the `gpt-4o-mini` row. The citation
resolves.

**After** (claude-haiku-4-5), same command, `PROVIDER=claude`:

> The series defaults to **`gpt-4o-mini`** for OpenAI and
> **`claude-haiku-4-5`** for Anthropic/Claude. For `gpt-4o-mini`: it's
> "Cheap, fast, multimodal — great for learning and most tasks"
> (MODELS.md:40). For `claude-haiku-4-5`: the Claude dives default to it "for
> cheap iteration" (MODELS.md:55). ...

Same contract, both providers; Haiku is wordier but every claim is cited.
(Note for v04's citation scorer: models may normalize `../MODELS.md` to
`MODELS.md` in citations; the scorer should compare resolved paths.)

## 3. Answer not in the provided context (v04 category: `negative`)

**Ask:** `How does the secrun wrapper store API keys?` `--context ../MODELS.md`
(the answer lives in SECRETS.md, not the provided file)

**After** (gpt-4o-mini):

> Not in this corpus. (Guess: information about storing API keys might be
> located in configuration files or README documentation.)

**After** (claude-haiku-4-5):

> Not in this corpus. The provided context is a models and pricing reference
> document. Information about API key storage would likely be in
> documentation about authentication or configuration, which isn't included
> here.

This is the retrieval-failure drill: when v03's retrieval fetches the wrong
chunks, this rule is what turns a hallucination into a visible miss.

## 4. Prior-knowledge temptation (v04 category: `negative`)

**Ask:** `What is the capital of France?` `--context ../MODELS.md`

**After** (gpt-4o-mini):

> Not in this corpus. (Guess: the capital of France might be mentioned in a
> geographical or cultural context document.)

The model *knows* the answer and declines anyway: grounding beats prior
knowledge. This is the sharpest test of rule 1 and the seed for v04's
off-topic questions.

---

## Reproduce

```bash
# before
secrun python -m askrepo ask "What chunk size should I use for RAG?" --raw
# after
secrun python -m askrepo ask "What chunk size should I use for RAG?"
secrun python -m askrepo ask "What model does the series default to, and why?" --context ../MODELS.md
```

Transcripts are snapshots, not assertions; models drift. v04 turns these
into scored, rerunnable checks.
