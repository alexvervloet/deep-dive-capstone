"""Providers: everything that can answer a question, behind one interface.

The contract every provider honors:

    complete(messages) -> iterator of text chunks (a stream)

where `messages` is the familiar [{"role": ..., "content": ...}, ...] list.
v00 ships only the mock — it proves the plumbing (CLI -> provider -> streamed
answer) with no key, no network, no cost. v01-chat adds OpenAI and Claude
behind this same interface, which is the whole point of having it.
"""


class MockProvider:
    """Answers offline with a canned response. Never calls a model.

    The mock is honest about being a mock: its answer says no model ran, so a
    reader can't mistake plumbing for intelligence. (It also echoes the
    question back — proof the text made the round trip.)
    """

    name = "mock"

    def complete(self, messages):
        question = ""
        for message in reversed(messages):
            if message["role"] == "user":
                question = message["content"]
                break
        answer = (
            "[mock] No model was called and no key was needed — this canned "
            "answer proves the plumbing works: your question "
            f"({question!r}) travelled CLI -> provider -> streamed answer.\n"
            "From v01-chat, PROVIDER=openai or PROVIDER=claude puts a real "
            "model in this seat."
        )
        # Stream word by word so the CLI's streaming path is exercised for
        # real — v01's providers will yield chunks exactly like this.
        for i, word in enumerate(answer.split(" ")):
            yield word if i == 0 else " " + word


PROVIDERS = {
    "mock": MockProvider,
    # "openai": ...   arrives at v01-chat
    # "claude": ...   arrives at v01-chat
}


def get_provider(name):
    if name in PROVIDERS:
        return PROVIDERS[name]()
    if name in ("openai", "claude"):
        raise SystemExit(
            f"PROVIDER={name} arrives at v01-chat — at v00 only 'mock' exists.\n"
            "Set PROVIDER=mock in .env (or leave .env out; mock is the default)."
        )
    raise SystemExit(
        f"PROVIDER={name!r} is not recognized. Use mock, openai, or claude."
    )
