"""Providers: everything that can answer a question, behind one interface.

The contract every provider honors:

    complete(messages) -> iterator of text chunks (a stream)

where `messages` is the familiar [{"role": ..., "content": ...}, ...] list.
After the stream is fully consumed, `provider.usage` holds the real
(input_tokens, output_tokens) for the call — that's what the CLI prices.

The SDKs are imported lazily inside each provider so the mock keeps working
on a machine with nothing installed. That's the v00 promise, kept.
"""

# $ per 1M tokens (input, output) — same numbers as ../MODELS.md, so the cost
# line here matches what the series teaches. Update both places together.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Embeddings have no output tokens — you pay input only ($ per 1M tokens).
# Anthropic has no first-party embeddings model; the claude stack uses Voyage
# (its own SDK and key), exactly as the RAG dive teaches.
EMBED_MODELS = {
    "openai": "text-embedding-3-small",
    "claude": "voyage-3.5",
}
EMBED_PRICES = {
    "text-embedding-3-small": 0.02,
    "voyage-3.5": 0.06,
}

MAX_TOKENS = 1024  # single-question answers; revisit when chat arrives


def _split_system(messages):
    """Split a leading {"role": "system"} message from the rest.

    The two APIs disagree here: OpenAI takes the system prompt as a message,
    Claude as a separate `system` parameter (a whole lesson in the Claude API
    dive). Providers that need the split call this; OpenAI passes messages
    through untouched.
    """
    if messages and messages[0]["role"] == "system":
        return messages[0]["content"], messages[1:]
    return None, messages


class MockProvider:
    """Answers offline with a canned response. Never calls a model.

    The mock is honest about being a mock: its answer says no model ran, so a
    reader can't mistake plumbing for intelligence. (It also echoes the
    question back — proof the text made the round trip.)
    """

    name = "mock"
    model = "canned-answer"

    def __init__(self):
        self.usage = (0, 0)

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
            "Set PROVIDER=openai or PROVIDER=claude to put a real model in "
            "this seat."
        )
        # Stream word by word so the CLI's streaming path is exercised for
        # real — the real providers below yield chunks exactly like this.
        for i, word in enumerate(answer.split(" ")):
            yield word if i == 0 else " " + word


class OpenAIProvider:
    """OpenAI chat completions, streamed. Needs OPENAI_API_KEY (via secrun)."""

    name = "openai"

    def __init__(self, model=None):
        self.model = model or "gpt-4o-mini"
        self.usage = (0, 0)

    def complete(self, messages):
        from openai import OpenAI

        client = OpenAI()
        stream = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=MAX_TOKENS,
            stream=True,
            # ask for a final usage chunk so the cost line is real, not guessed
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if chunk.usage:  # the last chunk: no choices, just the totals
                self.usage = (chunk.usage.prompt_tokens, chunk.usage.completion_tokens)


class ClaudeProvider:
    """Claude messages, streamed. Needs ANTHROPIC_API_KEY (via secrun)."""

    name = "claude"

    def __init__(self, model=None):
        self.model = model or "claude-haiku-4-5"
        self.usage = (0, 0)

    def complete(self, messages):
        import anthropic

        client = anthropic.Anthropic()
        system, messages = _split_system(messages)
        kwargs = {"system": system} if system else {}
        with client.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=messages,
            **kwargs,
        ) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
            self.usage = (final.usage.input_tokens, final.usage.output_tokens)


PROVIDERS = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
}


def get_provider(name, model=None):
    if name == "mock":
        return MockProvider()  # takes no model — it has no model to pick
    if name in PROVIDERS:
        return PROVIDERS[name](model=model or None)
    raise SystemExit(
        f"PROVIDER={name!r} is not recognized. Use mock, openai, or claude."
    )


def embed(texts, stack, input_type="document"):
    """Embed a batch of texts on the given stack ('openai' or 'claude').

    Returns (vectors, total_tokens). `input_type` is "document" for things
    you're storing, "query" for a search query — Voyage uses the hint to
    optimize each side of retrieval; OpenAI ignores it.

    The stack is an explicit argument (not read from PROVIDER) because the
    query at ask-time MUST be embedded with the same model the index was
    built with — vectors from different models live in different spaces and
    comparing them is meaningless. retrieve.py reads the stack out of the
    saved index and passes it here.
    """
    if not texts:
        return [], 0
    if stack == "openai":
        from openai import OpenAI

        resp = OpenAI().embeddings.create(
            model=EMBED_MODELS["openai"], input=list(texts)
        )
        return [item.embedding for item in resp.data], resp.usage.total_tokens
    if stack == "claude":
        import voyageai

        result = voyageai.Client().embed(
            list(texts), model=EMBED_MODELS["claude"], input_type=input_type
        )
        return result.embeddings, result.total_tokens
    raise SystemExit(
        f"No embedding stack for PROVIDER={stack!r} — the mock can't embed. "
        "Set PROVIDER=openai or PROVIDER=claude to build an index."
    )


def cost_usd(provider):
    """Real cost of the last call, or None if the model isn't in PRICES."""
    if provider.name == "mock":
        return 0.0
    if provider.model not in PRICES:
        return None
    input_price, output_price = PRICES[provider.model]
    input_tokens, output_tokens = provider.usage
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
