"""
Setup check: run this first.

    python check_setup.py            # PROVIDER=mock needs nothing else
    secrun python check_setup.py     # so it can see your keychain-stored key

Checks your Python version, your PROVIDER, the installed packages, and the
API key that provider needs, and tells you exactly what to fix. Makes NO API
calls. Uses only the standard library, so it runs even before `pip install`.

PROVIDER=mock still passes on a fresh clone with no .env, no key, and no
installed packages; the v00 promise holds at every tag.
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
    "local": [],  # no key; that's the point. Ollama runs on your machine
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
    fail(f"Python {major}.{minor}: this repo needs Python 3.10 or newer.")
    print("    Install a newer Python from https://www.python.org/downloads/")
    return False


def check_provider(env):
    print("\nProvider")
    if env is None:
        warn("No .env file; using defaults (PROVIDER=mock).")
        print("    To configure:  cp .env.example .env")
    provider = (_get(env, "PROVIDER") or "mock").strip().lower()
    if provider in PROVIDER_DEPS:
        note = " (offline, no key, no cost)" if provider == "mock" else ""
        ok(f"PROVIDER = {provider}{note}")
        return provider
    fail(f"PROVIDER = {provider!r} is not recognized.")
    print("    Set PROVIDER=mock, openai, or claude in .env.")
    return None


def check_dependencies(provider):
    print("\nDependencies")
    needed = PROVIDER_DEPS.get(provider, [])
    if not needed:
        ok("none for the mock; it's pure standard library.")
        return True
    missing = []
    for import_name, pip_name, purpose in needed:
        if importlib.util.find_spec(import_name) is not None:
            ok(f"{pip_name}: {purpose}")
        else:
            fail(f"{pip_name} MISSING: {purpose}")
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
        ok(f"none needed: {reason}")
        return True
    all_ok = True
    for name, prefix, placeholder in keys:
        value = _get(env, name)
        if not value or value == placeholder:
            fail(f"{name} is not set.")
            print("    Store it in your OS keychain and run `secrun python check_setup.py`. See ../SECRETS.md.")
            all_ok = False
        elif not value.startswith(prefix):
            warn(f"{name} is set but doesn't start with '{prefix}'. Double-check it.")
        else:
            ok(f"{name} is set and looks right.")
    return all_ok


def check_local_server(env):
    """For PROVIDER=local: is the OpenAI-compatible server reachable, and does it
    serve the configured chat + embed models?

    Works for any runner (Ollama, LM Studio, llama.cpp, vLLM...), on this box or
    another; it probes the standard `/v1/models` endpoint they all expose.
    Standard library only, so the no-install promise holds."""
    print("\nLocal model server")
    import json as _json
    import urllib.error
    import urllib.request

    base = _get(env, "LOCAL_BASE_URL") or (
        (_get(env, "OLLAMA_HOST") or "http://localhost:11434") + "/v1")
    chat_model = _get(env, "LOCAL_MODEL") or _get(env, "MODEL") or "qwen3"
    embed_model = _get(env, "LOCAL_EMBED_MODEL") or "nomic-embed-text"
    api_key = _get(env, "LOCAL_API_KEY")

    req = urllib.request.Request(f"{base}/models")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            served = {m.get("id", "") for m in _json.load(resp).get("data", [])}
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail(f"can't reach a local model server at {base} ({e}).")
        print("    Start your runner (Ollama / LM Studio / llama.cpp / vLLM). If")
        print("    it's on another machine, bind it to 0.0.0.0 and set LOCAL_BASE_URL.")
        return False
    ok(f"reached the server at {base} ({len(served)} models served).")
    all_ok = True
    for kind, model in (("chat", chat_model), ("embeddings", embed_model)):
        # exact id, or a forgiving substring match (runners vary on how they
        # advertise a loaded model's id vs the name you pass as `model`)
        if model in served or any(model in s or s in model for s in served if s):
            ok(f"{kind} model '{model}' is served.")
        else:
            fail(f"{kind} model '{model}' isn't in the served list.")
            print("    Load it in your runner, or check the exact id it advertises.")
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
    local = check_local_server(env) if provider == "local" else True

    print()
    if py and deps and keys and local:
        print(_c("All set! 🎉", "1;32"))
        if provider == "mock":
            print('Start here:  python -m askrepo ask "hello"')
            print("(The mock is offline and free; no key needed.)")
        elif provider == "local":
            print('Start here:  python -m askrepo index ..   then   '
                  'python -m askrepo ask "hello"')
            print("(Local models run on your machine; no key, no secrun, no bill.)")
        else:
            print('Start here:  secrun python -m askrepo ask "hello"')
        return 0
    print(_c("Not ready yet. Fix the ✗ items above, then run this again.", "1;31"))
    print("(PROVIDER=mock always works offline, no key needed.)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
