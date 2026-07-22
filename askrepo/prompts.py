"""The prompt contract: what askrepo's answering model is allowed to do.

Three rules, enforced by the system prompt and demonstrated by few-shots:

  1. Answer ONLY from the provided context, never from prior knowledge.
  2. Cite (path:line) for every claim, using the line numbers in the context.
  3. If the context doesn't contain the answer, say so with DECLINE_PHRASE 
     verbatim, so later steps can score declines mechanically (v04).

There is no retrieval yet; context arrives via `ask --context <file>`. That's
the point: the contract is testable before the pipeline exists, and these
rules are what retrieval (v03) will be plugged into.
"""

# Bump when the contract below changes: it's part of the answer cache key
# (v07), so editing the rules never serves an answer shaped by the old ones.
CONTRACT_VERSION = "2"

# Verbatim decline marker. The eval runner (v04) scores a `negative`-category
# question as correct iff the answer contains this exact phrase.
DECLINE_PHRASE = "Not in this corpus."

# Citation shape the contract asks for: (path:line) or (path:start-end).
# v04's citation-precision scorer parses exactly this format.
CITATION_FORMAT = "(path:line)"

SYSTEM_PROMPT = f"""\
You are askrepo, a codebase Q&A assistant. You answer questions about a code
repository using ONLY the context blocks provided in the conversation.

Rules: these override everything else:

1. Ground every claim in the provided context. Do not use prior knowledge,
   even when you are confident. If the context and your prior knowledge
   disagree, the context wins.
2. Cite the source of every claim as {CITATION_FORMAT}, e.g. (src/app.py:42)
   or (README.md:10-14), using the path and line numbers exactly as they
   appear in the context blocks. Every factual sentence needs at least one
   citation.
3. If the provided context does not contain the answer (or no context was
   provided at all) reply with exactly: {DECLINE_PHRASE} You may add one
   sentence suggesting where the answer might live, clearly marked as a
   guess. Never improvise an answer to be helpful.
4. Answer directly and concisely. No preamble like "Based on the context".
"""

# Few-shot examples: one grounded-and-cited answer, one decline. These teach
# the *shape* of a good answer more reliably than the rules alone: and the
# decline example is the one models most need to see (the helpful-improviser
# failure mode is the default without it).
FEW_SHOTS = [
    {
        "role": "user",
        "content": (
            '<context path="tools/fmt.py">\n'
            "1| MAX_WIDTH = 88\n"
            "2|\n"
            "3| def wrap(text):\n"
            '4|     """Wrap text to MAX_WIDTH columns."""\n'
            "5|     return textwrap.fill(text, MAX_WIDTH)\n"
            "</context>\n\n"
            "Question: What line width does the formatter use?"
        ),
    },
    {
        "role": "assistant",
        "content": (
            "The formatter wraps text to 88 columns; `MAX_WIDTH = 88` "
            "(tools/fmt.py:1), used by `wrap()` (tools/fmt.py:5)."
        ),
    },
    {
        "role": "user",
        "content": (
            '<context path="tools/fmt.py">\n'
            "1| MAX_WIDTH = 88\n"
            "</context>\n\n"
            "Question: Which linter does this project use?"
        ),
    },
    {
        "role": "assistant",
        "content": (
            f"{DECLINE_PHRASE} (Guess: a linter would likely be configured in "
            "pyproject.toml or a CI workflow, which aren't in the provided "
            "context.)"
        ),
    },
]


def format_context(path, text, start=1):
    """One context block, line-numbered so citations have something to cite.

    `start` is the text's first line number in the original file; retrieval
    (v03) passes chunks from the middle of files, and the citation must point
    at where the chunk actually lives, not at line 1.
    """
    numbered = "\n".join(
        f"{i}| {line}" for i, line in enumerate(text.splitlines(), start=start)
    )
    return f'<context path="{path}">\n{numbered}\n</context>'


def build_messages(question, context_blocks):
    """Assemble the full conversation: system contract, few-shots, the ask.

    `context_blocks` is a list of already-formatted blocks (see
    format_context). The system prompt rides as a {"role": "system"} message;
    each provider translates that to its API's shape (OpenAI: a system
    message; Claude: the separate `system` parameter).
    """
    parts = list(context_blocks)
    parts.append(f"Question: {question}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *FEW_SHOTS,
        {"role": "user", "content": "\n\n".join(parts)},
    ]
