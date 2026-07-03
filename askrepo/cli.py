"""The askrepo CLI.

    python -m askrepo ask "your question"

v00 wires exactly one path: ask -> provider -> streamed answer. Every later
step hangs new subcommands off this skeleton (index, chat, eval, redteam) —
see ../CAPSTONE.md for the roadmap.
"""

import argparse
import sys

from askrepo.config import load_config
from askrepo.providers import get_provider


def cmd_ask(args):
    config = load_config()
    provider = get_provider(config["PROVIDER"])

    messages = [{"role": "user", "content": args.question}]

    print(f"provider: {provider.name}", file=sys.stderr)
    for chunk in provider.complete(messages):
        print(chunk, end="", flush=True)
    # flush before the stderr cost line so the two streams can't interleave
    # when stdout is piped
    print(flush=True)
    # Cost transparency from day zero: every answer says what it cost. The
    # mock's honest number is zero; real providers report real numbers (v01+).
    print("cost: $0.0000 (mock makes no API calls)", file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="askrepo",
        description="Ask questions about a codebase, get answers with citations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="ask a single question")
    ask.add_argument("question", help="the question, in plain English")
    ask.set_defaults(func=cmd_ask)

    args = parser.parse_args(argv)
    return args.func(args)
