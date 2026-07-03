"""
Setup check — run this first.
=============================

    python check_setup.py

Checks your Python version, your PROVIDER, and (once real providers exist)
your packages and API key — and tells you exactly what to fix. Makes NO API
calls. Uses only the standard library, so it runs even before `pip install`.

At v00-scaffold the only provider is `mock`, so a fresh clone with no .env,
no key, and no installed packages should pass. That's the point of v00.
"""

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

# Providers the code actually ships at this tag. v01-chat adds the real two —
# and with them, the dependency and API-key sections this script grows next.
SHIPPED_PROVIDERS = ("mock",)
FUTURE_PROVIDERS = ("openai", "claude")


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
        print("    That's fine at v00. To configure:  cp .env.example .env")
    provider = (os.getenv("PROVIDER") or (env or {}).get("PROVIDER") or "mock").strip().lower()
    if provider in SHIPPED_PROVIDERS:
        ok(f"PROVIDER = {provider} (offline — no key, no cost)")
        return provider
    if provider in FUTURE_PROVIDERS:
        fail(f"PROVIDER = {provider} arrives at v01-chat; only 'mock' exists at v00.")
        print("    Set PROVIDER=mock in .env for now.")
        return None
    fail(f"PROVIDER = {provider!r} is not recognized.")
    print("    Set PROVIDER=mock in .env (openai/claude arrive at v01-chat).")
    return None


def check_dependencies():
    print("\nDependencies")
    ok("none at v00 — the mock provider is pure standard library.")
    print("    (Provider SDKs arrive at v01-chat, and this section grows teeth.)")
    return True


def main():
    print(_c("Checking your setup for the deep-dive capstone (v00-scaffold)...\n", "1"))
    env = _read_env_file()
    py = check_python()
    provider = check_provider(env)
    deps = check_dependencies()

    print()
    if py and provider and deps:
        print(_c("All set! 🎉", "1;32"))
        print('Start here:  python -m askrepo ask "hello"')
        print("(Everything at this tag is offline and free — no key needed.)")
        return 0
    print(_c("Not ready yet — fix the ✗ items above, then run this again.", "1;31"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
