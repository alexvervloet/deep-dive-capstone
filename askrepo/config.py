"""Read non-secret config (PROVIDER, MODEL) from .env, with safe defaults.

Standard library only, on purpose: v00 must run on a fresh clone before any
`pip install`. Real environment variables win over .env values, which is what
lets `secrun` inject keys per-command later without touching this file.
"""

import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "PROVIDER": "mock",
    "MODEL": "",  # each provider picks its own default; set here to override
    "BLEND": "0.7",  # hybrid retrieval: vector weight (1.0 = vector-only, 0.0 = keyword-only)
    "BUDGET": "0",   # per-session spend ceiling in USD; 0 = unlimited (v07)
}


def _read_env_file(path):
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def load_config():
    """Merge defaults <- .env <- real environment (strongest last)."""
    config = dict(DEFAULTS)
    config.update(_read_env_file(os.path.join(HERE, ".env")))
    for key in config:
        val = os.getenv(key)
        if val:
            config[key] = val
    config["PROVIDER"] = config["PROVIDER"].strip().lower()
    return config
