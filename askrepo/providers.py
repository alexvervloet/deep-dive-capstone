"""Providers: everything that can answer a question, behind one interface.

The contract every provider honors:

    complete(messages) -> iterator of text chunks (a stream)

where `messages` is the familiar [{"role": ..., "content": ...}, ...] list.
After the stream is fully consumed, `provider.usage` holds the real
(input_tokens, output_tokens) for the call; that's what the CLI prices.

v05 adds a second contract for the agent loop (see agent.py):

    step(messages, tools)          -> one non-streamed model turn: either
                                      {"kind": "text", ...} or
                                      {"kind": "tools", "calls": [...],
                                       "assistant": <message to append>}
    tool_results_messages(results) -> the message(s) that feed tool outputs
                                      back, in this provider's wire shape

The two APIs genuinely differ here (OpenAI: function-calling with role:"tool"
messages; Claude: tool_use/tool_result content blocks): the agents dive
teaches both shapes, and these methods are where the difference is contained.

The SDKs are imported lazily inside each provider so the mock keeps working
on a machine with nothing installed. That's the v00 promise, kept.
"""

import json
import os
from typing import Any

# The local backend is "point the OpenAI SDK somewhere that speaks the same
# wire format": which every runner does (Ollama, LM Studio, llama.cpp, vLLM,
# LocalAI...), on this box or another. So "local" isn't Ollama-specific; it's
# any OpenAI-compatible server (ext-local). Base-URL precedence:
#   LOCAL_BASE_URL   a full URL, used verbatim  (e.g. http://192.168.1.9:1234/v1)
#   OLLAMA_HOST      a host only, + "/v1"        (back-compat for a plain Ollama)
#   else             http://localhost:11434/v1   (local Ollama default)
# LOCAL_API_KEY sets a real bearer token when a runner or reverse-proxy wants
# one; the default "ollama" is a placeholder the SDK requires and local servers
# ignore. Embeddings may live on a DIFFERENT endpoint than chat (a runner that
# serves chat but not embeddings): LOCAL_EMBED_BASE_URL / LOCAL_EMBED_API_KEY
# override that side only, defaulting to the chat endpoint.


def _local_base_url():
    return os.getenv("LOCAL_BASE_URL") or (
        os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1")


def local_client_kwargs(embed=False) -> dict[str, Any]:
    """OpenAI-SDK kwargs pointing at the local server (chat or embeddings side)."""
    base = (os.getenv("LOCAL_EMBED_BASE_URL") if embed else None) or _local_base_url()
    key = ((os.getenv("LOCAL_EMBED_API_KEY") if embed else None)
           or os.getenv("LOCAL_API_KEY") or "ollama")
    return {"base_url": base, "api_key": key}

# $ per 1M tokens (input, output): same numbers as ../MODELS.md, so the cost
# line here matches what the series teaches. Update both places together.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Embeddings have no output tokens: you pay input only ($ per 1M tokens).
# Anthropic has no first-party embeddings model; the claude stack uses Voyage
# (its own SDK and key). The local stack uses a small Ollama embedding model 
# free, and the whole point of ext-local: index without sending a byte out.
EMBED_MODELS = {
    "openai": "text-embedding-3-small",
    "claude": "voyage-3.5",
    "local": os.getenv("LOCAL_EMBED_MODEL", "nomic-embed-text"),
}
EMBED_PRICES = {
    "text-embedding-3-small": 0.02,
    "voyage-3.5": 0.06,
    # local embeddings cost $0: it's your GPU, not a meter. Kept in the table
    # so the eval's cost column reads a true zero, not "unknown price".
    "nomic-embed-text": 0.0,
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
    question back, proof the text made the round trip.)
    """

    name = "mock"
    model = "canned-answer"

    def __init__(self):
        self.usage: tuple[int, int] = (0, 0)

    def complete(self, messages):
        question = ""
        for message in reversed(messages):
            if message["role"] == "user":
                question = message["content"]
                break
        answer = (
            "[mock] No model was called and no key was needed; this canned "
            "answer proves the plumbing works: your question "
            f"({question!r}) travelled CLI -> provider -> streamed answer.\n"
            "Set PROVIDER=openai or PROVIDER=claude to put a real model in "
            "this seat."
        )
        # Stream word by word so the CLI's streaming path is exercised for
        # real: the real providers below yield chunks exactly like this.
        for i, word in enumerate(answer.split(" ")):
            yield word if i == 0 else " " + word


class OpenAIProvider:
    """OpenAI chat completions, streamed. Needs OPENAI_API_KEY (via secrun)."""

    name = "openai"

    def __init__(self, model=None):
        self.model = model or "gpt-4o-mini"
        self.usage: tuple[int, int] = (0, 0)
        self.max_tokens = MAX_TOKENS

    def _client_kwargs(self):
        # OpenAI proper: no base_url, real key from OPENAI_API_KEY. LocalProvider
        # overrides this to point the very same SDK at a local server.
        return {}

    def _client(self):
        from openai import OpenAI

        return OpenAI(**self._client_kwargs())

    def complete(self, messages):
        stream = self._client().chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            stream=True,
            # ask for a final usage chunk so the cost line is real, not guessed
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if chunk.usage:  # the last chunk: no choices, just the totals
                self.usage = (chunk.usage.prompt_tokens, chunk.usage.completion_tokens)

    def step(self, messages, tools):
        kwargs = {}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
        resp = self._client().chat.completions.create(
            model=self.model, messages=messages, max_tokens=self.max_tokens, **kwargs
        )
        self.usage = (resp.usage.prompt_tokens, resp.usage.completion_tokens)
        msg = resp.choices[0].message
        if msg.tool_calls:
            return {
                "kind": "tools",
                "calls": [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "args": json.loads(tc.function.arguments or "{}"),
                    }
                    for tc in msg.tool_calls
                ],
                "assistant": {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                },
            }
        return {"kind": "text", "text": msg.content or ""}

    def tool_results_messages(self, results):
        return [
            {"role": "tool", "tool_call_id": call_id, "content": output}
            for call_id, output in results
        ]


class ClaudeProvider:
    """Claude messages, streamed. Needs ANTHROPIC_API_KEY (via secrun)."""

    name = "claude"

    def __init__(self, model=None):
        self.model = model or "claude-haiku-4-5"
        self.usage: tuple[int, int] = (0, 0)

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

    def step(self, messages, tools):
        import anthropic

        system, msgs = _split_system(messages)
        kwargs = {"system": system} if system else {}
        if tools:
            kwargs["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]
        resp = anthropic.Anthropic().messages.create(
            model=self.model, max_tokens=MAX_TOKENS, messages=msgs, **kwargs
        )
        self.usage = (resp.usage.input_tokens, resp.usage.output_tokens)
        if resp.stop_reason == "tool_use":
            return {
                "kind": "tools",
                "calls": [
                    {"id": b.id, "name": b.name, "args": b.input}
                    for b in resp.content
                    if b.type == "tool_use"
                ],
                # echo the content blocks back verbatim on the next turn 
                # the API requires the assistant turn to precede tool_results
                "assistant": {"role": "assistant", "content": resp.content},
            }
        return {
            "kind": "text",
            "text": "".join(b.text for b in resp.content if b.type == "text"),
        }

    def tool_results_messages(self, results):
        # all results ride in ONE user message of tool_result blocks
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call_id, "content": output}
                    for call_id, output in results
                ],
            }
        ]


class LocalProvider(OpenAIProvider):
    """A local model via Ollama's OpenAI-compatible endpoint (ext-local).

    Reuses every line of OpenAIProvider (streaming, tool-calling (`step`),
    usage accounting) and changes exactly one thing: where the SDK points.
    That is the local dive's whole thesis, so it's the whole implementation.
    The default chat model is overridable (LOCAL_MODEL / MODEL), because which
    model you loaded is your choice; check your runner's model list for the
    exact id (Ollama: `ollama list`; LM Studio/vLLM: the `/v1/models` endpoint).

    Tool-calling in `step()` works only if the loaded model supports it; many
    small local models don't, so agent mode may degrade. Reported, not hidden.
    """

    name = "local"

    def __init__(self, model=None):
        self.model = model or os.getenv("LOCAL_MODEL", "qwen3")
        self.usage: tuple[int, int] = (0, 0)
        # Thinking models (qwen3, deepseek-r1...) spend output tokens *reasoning*
        # before the answer: a 1024 cap can be fully consumed by reasoning,
        # leaving content empty (reasoning lands in a separate `reasoning_content`
        # field this client ignores). So local gets a generous, overridable
        # budget. Harmless for non-thinking models: it's a ceiling, they stop
        # early. If a thinking model still returns blank, raise LOCAL_MAX_TOKENS.
        self.max_tokens = int(os.getenv("LOCAL_MAX_TOKENS", "8192"))

    def _client_kwargs(self):
        return local_client_kwargs()


PROVIDERS = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "local": LocalProvider,
}


def get_provider(name, model=None):
    if name == "mock":
        return MockProvider()  # takes no model; it has no model to pick
    if name in PROVIDERS:
        return PROVIDERS[name](model=model or None)
    raise SystemExit(
        f"PROVIDER={name!r} is not recognized. Use mock, openai, claude, or local."
    )


def embed(texts, stack, input_type="document"):
    """Embed a batch of texts on the given stack ('openai', 'claude', 'local').

    Returns (vectors, total_tokens). `input_type` is "document" for things
    you're storing, "query" for a search query; Voyage uses the hint to
    optimize each side of retrieval; OpenAI and the local model ignore it.

    The stack is an explicit argument (not read from PROVIDER) because the
    query at ask-time MUST be embedded with the same model the index was
    built with; vectors from different models live in different spaces and
    comparing them is meaningless. retrieve.py reads the stack out of the
    saved index and passes it here. (A local-built index therefore stays
    local at query time too; no OpenAI/Voyage key involved.)
    """
    if not texts:
        return [], 0
    if stack in ("openai", "local"):
        from openai import OpenAI

        client = OpenAI(**(local_client_kwargs(embed=True) if stack == "local" else {}))
        resp = client.embeddings.create(model=EMBED_MODELS[stack], input=list(texts))
        # Ollama may omit usage; fall back to a token estimate so callers that
        # price/log it don't crash (local is free anyway).
        used = getattr(resp, "usage", None)
        total = used.total_tokens if used else sum(len(t) // 4 for t in texts)
        return [item.embedding for item in resp.data], total
    if stack == "claude":
        try:
            import voyageai
        except ModuleNotFoundError:
            raise SystemExit(
                "PROVIDER=claude embeds with Voyage, but the 'voyageai' package "
                "isn't installed. Run `pip install voyageai` (it's in "
                "requirements.txt) and set VOYAGE_API_KEY; `python check_setup.py` "
                "checks both."
            )

        result = voyageai.Client().embed(  # type: ignore[reportPrivateImportUsage]
            list(texts), model=EMBED_MODELS["claude"], input_type=input_type
        )
        return result.embeddings, result.total_tokens
    raise SystemExit(
        f"No embedding stack for PROVIDER={stack!r}; the mock can't embed. "
        "Set PROVIDER=openai, claude, or local to build an index."
    )


def cost_usd(provider):
    """Real cost of the last call, or None if the model isn't in PRICES."""
    if provider.name in ("mock", "local"):
        return 0.0  # mock never calls; local runs on your hardware, not a meter
    if provider.model not in PRICES:
        return None
    input_price, output_price = PRICES[provider.model]
    input_tokens, output_tokens = provider.usage
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
