"""
Setup check — run this first.
=============================

    python check_setup.py            # PROVIDER=mock needs nothing else
    secrun python check_setup.py     # so it can see your keychain-stored key

Checks your Python version, your PROVIDER, the installed packages, and the
API key that provider needs — and tells you exactly what to fix. Makes NO API
calls. Uses only the standard library, so it runs even before `pip install`.

PROVIDER=mock still passes on a fresh clone with no .env, no key, and no
installed packages — the v00 promise holds at every tag.
"""

import importlib.util
import os
import sys

_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _c(text, code):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def ok(msg):
    print(f"  {_c('✓', '32')} {msg}")


def warn(msg):
    print(f"  {_c('!', '33')} {msg}")


def fail(msg):
    print(f"  {_c('✗', '31')} {msg}")


HERE = os.path.dirname(os.path.abspath(__file__))

PROVIDER_DEPS = {
    "mock": [],
    "openai": [("openai", "openai", "OpenAI chat + embeddings")],
    "claude": [
        ("anthropic", "anthropic", "Claude messages, streamed"),
        ("voyageai", "voyageai", "Voyage embeddings (the claude stack's index)"),
    ],
    # local (ext-local) reuses the openai SDK, pointed at Ollama's port
    "local": [("openai", "openai", "the SDK, pointed at Ollama's OpenAI endpoint")],
}
PROVIDER_KEYS = {
    "mock": [],
    "openai": [("OPENAI_API_KEY", "sk-", "sk-your-openai-key-here")],
    "claude": [
        ("ANTHROPIC_API_KEY", "sk-ant-", "sk-ant-your-key-here"),
        ("VOYAGE_API_KEY", "pa-", "pa-your-voyage-key-here"),
    ],
    "local": [],  # no key — that's the point; Ollama runs on your machine
}


def _read_env_file():
    env_path = os.path.join(HERE, ".env")
    values = {}
    if not os.path.exists(env_path):
        return None
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _get(env, name):
    return os.getenv(name) or (env or {}).get(name, "")


def check_python():
    print("Python version")
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        ok(f"Python {major}.{minor} (3.10+ required)")
        return True
    fail(f"Python {major}.{minor} — this repo needs Python 3.10 or newer.")
    print("    Install a newer Python from https://www.python.org/downloads/")
    return False


def check_provider(env):
    print("\nProvider")
    if env is None:
        warn("No .env file — using defaults (PROVIDER=mock).")
        print("    To configure:  cp .env.example .env")
    provider = (_get(env, "PROVIDER") or "mock").strip().lower()
    if provider in PROVIDER_DEPS:
        note = " (offline — no key, no cost)" if provider == "mock" else ""
        ok(f"PROVIDER = {provider}{note}")
        return provider
    fail(f"PROVIDER = {provider!r} is not recognized.")
    print("    Set PROVIDER=mock, openai, or claude in .env.")
    return None


def check_dependencies(provider):
    print("\nDependencies")
    needed = PROVIDER_DEPS.get(provider, [])
    if not needed:
        ok("none for the mock — it's pure standard library.")
        return True
    missing = []
    for import_name, pip_name, purpose in needed:
        if importlib.util.find_spec(import_name) is not None:
            ok(f"{pip_name} — {purpose}")
        else:
            fail(f"{pip_name} MISSING — {purpose}")
            missing.append(pip_name)
    if missing:
        print("\n    Install everything with:")
        print("        pip install -r requirements.txt")
    return not missing


def check_keys(env, provider):
    print("\nAPI key")
    keys = PROVIDER_KEYS.get(provider, [])
    if not keys:
        reason = {
            "mock": "the mock never calls a model.",
            "local": "local models run on your machine, not a paid API.",
        }.get(provider, "this provider needs no key.")
        ok(f"none needed — {reason}")
        return True
    all_ok = True
    for name, prefix, placeholder in keys:
        value = _get(env, name)
        if not value or value == placeholder:
            fail(f"{name} is not set.")
            print("    Store it in your OS keychain and run `secrun python check_setup.py` — see ../SECRETS.md.")
            all_ok = False
        elif not value.startswith(prefix):
            warn(f"{name} is set but doesn't start with '{prefix}'. Double-check it.")
        else:
            ok(f"{name} is set and looks right.")
    return all_ok


def check_ollama(env):
    """For PROVIDER=local: is Ollama up, and are the chat + embed models pulled?

    Reaches the local server with the standard library only (no `requests`),
    so the no-install promise holds. This is the one 'key' the local path has:
    a running server with the right models."""
    print("\nOllama (local models)")
    import json as _json
    import urllib.error
    import urllib.request

    host = _get(env, "OLLAMA_HOST") or "http://localhost:11434"
    chat_model = _get(env, "LOCAL_MODEL") or _get(env, "MODEL") or "qwen3"
    embed_model = _get(env, "LOCAL_EMBED_MODEL") or "nomic-embed-text"
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            tags = _json.load(resp)
    except (urllib.error.URLError, OSError):
        fail(f"can't reach Ollama at {host}.")
        print("    Start it:  ollama serve   (or launch the Ollama app)")
        return False
    ok(f"Ollama is running at {host}.")
    present = {m.get("name", "").split(":")[0] for m in tags.get("models", [])}
    all_ok = True
    for kind, model in (("chat", chat_model), ("embeddings", embed_model)):
        if model.split(":")[0] in present:
            ok(f"{kind} model '{model}' is pulled.")
        else:
            fail(f"{kind} model '{model}' is not pulled.")
            print(f"    Pull it:  ollama pull {model}")
            all_ok = False
    if not all_ok:
        print("    (Both are needed: RAG embeds the corpus AND answers from it.)")
    return all_ok


def main():
    print(_c("Checking your setup for the deep-dive capstone...\n", "1"))
    env = _read_env_file()
    py = check_python()
    provider = check_provider(env)
    if provider is None:
        print(_c("\nFix PROVIDER in .env, then run this again.", "1;31"))
        return 1
    deps = check_dependencies(provider)
    keys = check_keys(env, provider)
    local = check_ollama(env) if provider == "local" else True

    print()
    if py and deps and keys and local:
        print(_c("All set! 🎉", "1;32"))
        if provider == "mock":
            print('Start here:  python -m askrepo ask "hello"')
            print("(The mock is offline and free — no key needed.)")
        elif provider == "local":
            print('Start here:  python -m askrepo index ..   then   '
                  'python -m askrepo ask "hello"')
            print("(Local models run on your machine — no key, no secrun, no bill.)")
        else:
            print('Start here:  secrun python -m askrepo ask "hello"')
        return 0
    print(_c("Not ready yet — fix the ✗ items above, then run this again.", "1;31"))
    print("(PROVIDER=mock always works offline, no key needed.)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
