"""The askrepo CLI.

    python -m askrepo ask "your question" --context some/file.py

v00 wired exactly one path: ask -> provider -> streamed answer. v02 put the
prompt contract on that path: answers are grounded in --context files, cited
(path:line), and declined when the context doesn't cover them. Later steps
hang new subcommands off this skeleton (index, chat, eval, redteam) — see
../CAPSTONE.md for the roadmap.
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
    else:
        context_blocks = []
        for path in args.context:
            with open(path, encoding="utf-8") as f:
                context_blocks.append(format_context(path, f.read()))
        messages = build_messages(args.question, context_blocks)

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
    ask.set_defaults(func=cmd_ask)

    args = parser.parse_args(argv)
    return args.func(args)
