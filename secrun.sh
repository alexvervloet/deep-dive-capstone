#!/bin/sh
# The zsh `secrun` function from ../SECRETS.md, as a script.
#
# Why both exist: MCP hosts (Claude Code, Claude Desktop) spawn servers as
# bare subprocesses — no interactive shell, so shell functions don't exist
# there. Same move, either way: pull the keys out of the macOS Keychain,
# export them into ONE process, exec. Nothing secret ever touches disk.
#
#     ./secrun.sh .venv/bin/python -m askrepo.mcp_server
#
# .mcp.json points Claude Code at exactly that command.

set -eu

[ $# -gt 0 ] || { echo "usage: secrun.sh <command> [args...]" >&2; exit 2; }

for key in ANTHROPIC_API_KEY OPENAI_API_KEY VOYAGE_API_KEY; do
    if ! value=$(security find-generic-password -a "$USER" -s "deepdives:$key" -w 2>/dev/null); then
        echo "secrun.sh: missing Keychain item 'deepdives:$key' — see ../SECRETS.md" >&2
        exit 1
    fi
    export "$key=$value"
done

# Optional keys: injected when the Keychain item exists, skipped silently when
# it doesn't (only some projects need them — e.g. LangSmith tracing).
for key in LANGSMITH_API_KEY; do
    if value=$(security find-generic-password -a "$USER" -s "deepdives:$key" -w 2>/dev/null); then
        export "$key=$value"
    fi
done

exec "$@"
