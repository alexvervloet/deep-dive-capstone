"""Agentic retrieval: answer by *searching* the corpus, not embedding it.

An agent is a loop: the model picks a tool, you run it, you feed the result
back: until it answers (the agents dive's one big idea, adapted from
agents-deep-dive/agent/loop.py + tools.py). Here the tools are read-only
corpus search:

    list_dir(path)                    orient
    grep(pattern[, path])             find candidates by exact words
    read_file(path, start_line)      read numbered lines around a hit

No vectors anywhere. This is the other answer to "how do I put the right
text in front of the model," and whether it beats the v03 pipeline is not
a matter of opinion: `run_evals.py --mode agent` measures both against the
same golden set and baseline.

Every tool output that shows file content shows it with `path:line` numbers,
so the v02 citation contract carries over unchanged. Output caps keep any
single tool result from flooding the context window.

Boundaries live in the harness (feat/harness, from harness.py): a permission
policy decides *which* tools run, a read-only sandbox decides *what* they may
touch, and an audit log records every proposed call and its verdict. v05's
inline path jail became the sandbox's first rule; the rules it lacked 
read_file could open any file inside the jail, a planted .env included 
are now enforced in code the model can't argue with. Refusals go back to the
model in-band ("error: ...") so the loop continues; the harness never crashes
an answer, it just narrows what one can do.
"""

import os
import re

from askrepo.harness import SandboxError, default_harness
from askrepo.indexer import HERE as CAPSTONE_ROOT
from askrepo.indexer import INDEXED_EXTENSIONS, SKIP_DIRS
from askrepo.prompts import DECLINE_PHRASE
from askrepo.providers import cost_usd

MAX_TOOL_CALLS = 12
GREP_MAX_HITS = 40
READ_MAX_LINES = 100

AGENT_SYSTEM = f"""\
You are askrepo, a codebase Q&A assistant. Answer questions about the
repository you can explore with your tools. Strategy that works: grep for
the most distinctive words of the question first (module names, exact
phrases); then read_file around the promising hits; use list_dir only when
you don't know where to look. You have a budget of {MAX_TOOL_CALLS} tool
calls: search efficiently, then answer.

Rules (these override everything else):

1. Ground every claim in file content you actually read this conversation.
2. Cite every claim as (path:line) or (path:start-end), using the line
   numbers shown in tool output. Every factual sentence needs a citation.
3. If you cannot find the answer after searching, reply with exactly:
   {DECLINE_PHRASE} You may add one sentence on where you looked.
4. Answer directly and concisely. No preamble.
"""

TOOL_SPECS = [
    {
        "name": "grep",
        "description": (
            "Regex search across all .md and .py files in the repository "
            "(case-insensitive). Returns matching lines as path:line: text. "
            "Best first move for distinctive terms."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "regular expression"},
                "path": {
                    "type": "string",
                    "description": "optional subdirectory or file to search in",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": (
            f"Read up to {READ_MAX_LINES} numbered lines of a file, starting "
            "at start_line. Cite from these numbers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file path from repo root"},
                "start_line": {
                    "type": "integer",
                    "description": "first line to read (default 1)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List a directory: subdirectories (with /) and files.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "directory path from repo root; '.' for the root",
                }
            },
            "required": ["path"],
        },
    },
]


def _skipped(dirpath, name):
    if name in SKIP_DIRS:
        return True
    return os.path.samefile(os.path.join(dirpath, name), CAPSTONE_ROOT)


def tool_grep(sandbox, pattern, path=None):
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"error: bad regex: {e}"
    corpus_root = sandbox.root
    base = sandbox.resolve(path)
    hits = []
    targets = []
    if os.path.isfile(base):
        targets = [base]
    else:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not _skipped(dirpath, d))
            targets.extend(
                os.path.join(dirpath, f)
                for f in sorted(filenames)
                if os.path.splitext(f)[1] in INDEXED_EXTENSIONS
            )
    for full in targets:
        rel = os.path.relpath(full, corpus_root)
        try:
            with open(full, encoding="utf-8") as f:
                for lineno, line in enumerate(f, start=1):
                    if rx.search(line):
                        hits.append(f"{rel}:{lineno}: {line.rstrip()[:200]}")
                        if len(hits) >= GREP_MAX_HITS:
                            hits.append(f"... truncated at {GREP_MAX_HITS} hits; narrow the pattern")
                            return "\n".join(hits)
        except (UnicodeDecodeError, OSError):
            continue
    return "\n".join(hits) if hits else "no matches"


def tool_read_file(sandbox, path, start_line=1):
    # the sandbox owns every read rule: jail, suffix allowlist, dotfile refusal
    lines = sandbox.read_text(path).splitlines()
    start = max(1, int(start_line or 1))
    window = lines[start - 1 : start - 1 + READ_MAX_LINES]
    if not window:
        return f"error: {path} has only {len(lines)} lines"
    body = "\n".join(f"{i}| {line}" for i, line in enumerate(window, start=start))
    if start - 1 + READ_MAX_LINES < len(lines):
        body += f"\n... file continues to line {len(lines)}"
    return body


def tool_list_dir(sandbox, path):
    full = sandbox.resolve(path)
    if not os.path.isdir(full):
        return f"error: {path!r} is not a directory"
    entries = []
    for name in sorted(os.listdir(full)):
        if name.startswith(".") or _skipped(full, name):
            continue
        entries.append(name + "/" if os.path.isdir(os.path.join(full, name)) else name)
    return "\n".join(entries) if entries else "(empty)"


def run_tool(harness, name, args, touched):
    """One proposed call through the full boundary: policy, then sandbox.

    Every refusal returns as in-band `error: ...` text; the model gets to
    see why and try something legitimate instead; the loop never dies on a
    hostile suggestion.
    """
    from askrepo.harness import ALLOW, DENY

    if harness.decide(name, args) != ALLOW:
        return f"error: tool {name!r} denied by permission policy"
    try:
        if name == "grep":
            out = tool_grep(harness.sandbox, args.get("pattern", ""), args.get("path"))
            for line in out.splitlines():
                if ":" in line and not line.startswith(("error", "no matches", "...")):
                    touched.add(line.split(":", 1)[0])
            return out
        if name == "read_file":
            out = tool_read_file(harness.sandbox, args.get("path", ""), args.get("start_line", 1))
            if not out.startswith("error"):  # a refused read didn't touch anything
                touched.add(os.path.normpath(args.get("path", "")))
            return out
        if name == "list_dir":
            return tool_list_dir(harness.sandbox, args.get("path", "."))
        return f"error: unknown tool {name!r}"
    except SandboxError as e:
        # the policy allowed the tool, but the sandbox refused these arguments 
        # amend the audit trail so the flight recorder shows the block
        harness.audit.record(name, args, DENY, note=f"sandbox: {e}")
        return f"error: {e}"
    except (ValueError, OSError) as e:
        return f"error: {e}"


def answer(question, corpus_root, provider, on_tool=None, harness=None):
    """Run the loop until the model answers or the tool budget runs out.

    Returns (answer_text, touched_paths, n_tool_calls, cost_usd_total).
    `touched` (files the agent grepped hits in or read) is the agent-mode
    analogue of "retrieved" for hit@k scoring (a generous analogue: touching
    a file isn't proof the model used it).

    `harness` defaults to harness.default_harness(corpus_root): the boundary
    is on unless a caller (the red-team's before-picture) hands in another.
    """
    harness = harness or default_harness(corpus_root)
    messages = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": f"Question: {question}"},
    ]
    touched = set()
    n_calls = 0
    total_cost = 0.0

    while n_calls < MAX_TOOL_CALLS:
        result = provider.step(messages, TOOL_SPECS)
        total_cost += cost_usd(provider) or 0.0
        if result["kind"] == "text":
            return result["text"], sorted(touched), n_calls, total_cost
        messages.append(result["assistant"])
        outputs = []
        for call in result["calls"]:
            output = run_tool(harness, call["name"], call["args"], touched)
            outputs.append((call["id"], output))
            n_calls += 1
            if on_tool:
                on_tool(call["name"], call["args"])
        messages.extend(provider.tool_results_messages(outputs))

    # budget exhausted: one last turn with no tools forces a text answer
    messages.append({
        "role": "user",
        "content": (
            "Tool budget exhausted. Answer now from what you have read, "
            f"with citations, or reply with: {DECLINE_PHRASE}"
        ),
    })
    result = provider.step(messages, tools=None)
    total_cost += cost_usd(provider) or 0.0
    return result.get("text", ""), sorted(touched), n_calls, total_cost
