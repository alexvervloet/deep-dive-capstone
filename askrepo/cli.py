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
import os
import sys

from askrepo.config import load_config
from askrepo.prompts import build_messages, format_context
from askrepo.providers import cost_usd, get_provider


def _produce(args, config, provider, trace):
    """Run the requested mode, streaming to stdout, and return (text, cost).

    All the v01-v05 answer paths live here; cmd_ask wraps this with the v07
    ops layer (cache, budget, trace) so those concerns stay in one place.
    """
    print(f"provider: {provider.name} ({provider.model})", file=sys.stderr)

    if args.mode == "agent" and provider.name != "mock":
        from askrepo.agent import answer as agent_answer
        from askrepo.harness import default_harness
        from askrepo.retrieve import load_index

        with trace.span("agent_loop"):
            corpus_root = load_index()["corpus_root"]
            harness = default_harness(corpus_root)
            text, touched, n_calls, cost = agent_answer(
                args.question, corpus_root, provider,
                on_tool=lambda name, targs: print(
                    f"tool: {name}({', '.join(f'{k}={v!r}' for k, v in targs.items())})",
                    file=sys.stderr,
                ),
                harness=harness,
            )
        print(text, flush=True)
        denied = harness.audit.denied
        print(f"cost: ${cost:.6f} ({n_calls} tool calls, "
              f"{len(touched)} files touched, {len(denied)} denied by harness)",
              file=sys.stderr)
        trace.set(mode="agent", tool_calls=n_calls, cost_usd=round(cost, 6))
        return text, cost

    # build the messages for the requested grounding
    if args.raw:
        messages = [{"role": "user", "content": args.question}]
    elif args.context:
        blocks = []
        for path in args.context:
            with open(path, encoding="utf-8") as f:
                blocks.append(format_context(path, f.read()))
        messages = build_messages(args.question, blocks)
    elif provider.name == "mock":
        messages = build_messages(args.question, [])  # offline plumbing check
    else:
        from askrepo.answer import prepare

        with trace.span("retrieve"):
            messages, sources = prepare(
                args.question, k=args.k, blend=float(config["BLEND"])
            )
        for score, chunk in sources:
            print(f"retrieved: {chunk['path']}:{chunk['start_line']}-"
                  f"{chunk['end_line']} (score {score:.2f})", file=sys.stderr)

    parts = []
    with trace.span("generate"):
        for piece in provider.complete(messages):
            print(piece, end="", flush=True)
            parts.append(piece)
    print(flush=True)  # flush before the stderr line so streams don't interleave

    cost = cost_usd(provider)
    in_tok, out_tok = provider.usage
    if cost is None:
        print(f"tokens: {in_tok} in / {out_tok} out "
              f"(no price on file for {provider.model} — see ../MODELS.md)",
              file=sys.stderr)
        cost = 0.0
    else:
        # six decimals: a cheap real call is ~$0.00002; "$0.0000" would lie
        # that it was free — only the mock prints a true zero
        print(f"cost: ${cost:.6f} ({in_tok} in / {out_tok} out)", file=sys.stderr)
    trace.set(mode=args.mode, input_tokens=in_tok, output_tokens=out_tok,
              cost_usd=round(cost, 6))
    return "".join(parts), cost


def cmd_ask(args):
    from askrepo.ops import Budget, BudgetExceeded, ResponseCache, cache_key, start_trace
    from askrepo.prompts import CONTRACT_VERSION

    config = load_config()
    provider = get_provider(config["PROVIDER"], model=config["MODEL"])

    # cache key over everything that shapes the answer: any change busts it
    key = cache_key(
        provider.name, provider.model, CONTRACT_VERSION, args.mode,
        args.k, config["BLEND"], bool(args.raw), tuple(args.context), args.question,
    )
    cache = ResponseCache()
    budget = Budget(float(config["BUDGET"]))

    with start_trace("ask") as trace:
        trace.set(question=args.question, cache_key=key)
        if not args.no_cache:
            cached = cache.get(key)
            if cached is not None:
                print(f"provider: {provider.name} (cache hit)", file=sys.stderr)
                print(cached, flush=True)
                print("cost: $0.000000 (served from cache)", file=sys.stderr)
                trace.set(cache="hit", cost_usd=0.0)
                return 0
        trace.set(cache="miss")

        try:
            budget.check()  # refuse to start a call once the ceiling is hit
        except BudgetExceeded as e:
            print(f"budget: {e}", file=sys.stderr)
            return 2

        text, cost = _produce(args, config, provider, trace)
        budget.record(cost)
        if not args.no_cache and provider.name != "mock" and text.strip():
            cache.set(key, text)
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


def cmd_redteam(args):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
    import redteam

    return redteam.run(args.freeze)


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
    ask.add_argument(
        "--mode",
        choices=["rag", "agent"],
        default="rag",
        help="rag: embed + retrieve (v03); agent: grep/read tool loop (v05)",
    )
    ask.add_argument(
        "--no-cache",
        action="store_true",
        help="skip the answer cache — always call the model (v07)",
    )
    ask.set_defaults(func=cmd_ask)

    index = subparsers.add_parser(
        "index", help="chunk and embed a corpus directory"
    )
    index.add_argument("path", help="root of the corpus to index (e.g. ..)")
    index.set_defaults(func=cmd_index)

    redteam = subparsers.add_parser(
        "redteam", help="attack askrepo with the poisoned fixtures corpus (v06)"
    )
    redteam.add_argument(
        "--freeze", action="store_true", help="write evals/redteam.result.json"
    )
    redteam.set_defaults(func=cmd_redteam)

    args = parser.parse_args(argv)
    return args.func(args)
