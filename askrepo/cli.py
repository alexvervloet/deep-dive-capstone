"""The askrepo CLI.

    secrun python -m askrepo index ..          # embed the corpus, once
    secrun python -m askrepo ask "question"    # retrieve, answer, cite

v00 wired exactly one path: ask -> provider -> streamed answer. v02 put the
prompt contract on that path (grounded, cited, declined otherwise). v03 made
the grounding automatic: `index` builds a hybrid-searchable index, and `ask`
retrieves its own context. `--context <file>` still overrides retrieval for
hand-fed grounding. Later steps hang new subcommands off this skeleton (chat,
eval, redteam) — see ../CAPSTONE.md for the roadmap.
"""

import argparse
import sys

from askrepo.config import load_config
from askrepo.prompts import build_messages, format_context
from askrepo.providers import cost_usd, get_provider


def cmd_ask(args):
    config = load_config()
    provider = get_provider(config["PROVIDER"], model=config["MODEL"])

    if args.raw:
        # contract off — the "before" picture. Kept so anyone can reproduce
        # the regression transcripts in evals/prompt_regression.md.
        messages = [{"role": "user", "content": args.question}]
    elif args.context:
        # hand-fed grounding (the v02 path) — overrides retrieval
        context_blocks = []
        for path in args.context:
            with open(path, encoding="utf-8") as f:
                context_blocks.append(format_context(path, f.read()))
        messages = build_messages(args.question, context_blocks)
    elif provider.name == "mock":
        # the mock can't embed a query; it stays the offline plumbing check
        messages = build_messages(args.question, [])
    else:
        from askrepo.answer import prepare

        messages, sources = prepare(
            args.question, k=args.k, blend=float(config["BLEND"])
        )
        # show what was retrieved — retrieval is never a black box
        for score, chunk in sources:
            print(
                f"retrieved: {chunk['path']}:{chunk['start_line']}-"
                f"{chunk['end_line']} (score {score:.2f})",
                file=sys.stderr,
            )

    print(f"provider: {provider.name} ({provider.model})", file=sys.stderr)
    for chunk in provider.complete(messages):
        print(chunk, end="", flush=True)
    # flush before the stderr cost line so the two streams can't interleave
    # when stdout is piped
    print(flush=True)
    # Cost transparency from day zero: every answer says what it cost, priced
    # from the provider's real token usage (the mock's honest number is zero).
    cost = cost_usd(provider)
    input_tokens, output_tokens = provider.usage
    if cost is None:
        print(
            f"tokens: {input_tokens} in / {output_tokens} out "
            f"(no price on file for {provider.model} — see ../MODELS.md)",
            file=sys.stderr,
        )
    else:
        # six decimals: a cheap real call is ~$0.00002, and "$0.0000" would
        # lie that it was free — only the mock gets to print a true zero
        print(
            f"cost: ${cost:.6f} ({input_tokens} in / {output_tokens} out)",
            file=sys.stderr,
        )
    return 0


def cmd_index(args):
    from askrepo.indexer import INDEX_PATH, build_index

    config = load_config()
    stack = config["PROVIDER"]
    n_files, n_chunks, tokens, cost = build_index(args.path, stack)
    print(
        f"indexed {n_files} files -> {n_chunks} chunks "
        f"({tokens} embedding tokens, ${cost:.4f})",
        file=sys.stderr,
    )
    print(f"saved: {INDEX_PATH}", file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="askrepo",
        description="Ask questions about a codebase, get answers with citations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="ask a single question")
    ask.add_argument("question", help="the question, in plain English")
    ask.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="FILE",
        help="file to ground the answer in (repeatable); its path becomes the citation label",
    )
    ask.add_argument(
        "--raw",
        action="store_true",
        help="bypass the prompt contract (no grounding, no citations) — the 'before' picture",
    )
    ask.add_argument(
        "--k",
        type=int,
        default=5,
        help="how many chunks to retrieve (default 5)",
    )
    ask.set_defaults(func=cmd_ask)

    index = subparsers.add_parser(
        "index", help="chunk and embed a corpus directory"
    )
    index.add_argument("path", help="root of the corpus to index (e.g. ..)")
    index.set_defaults(func=cmd_index)

    args = parser.parse_args(argv)
    return args.func(args)
