"""Glue: retrieve -> prompt contract -> messages ready for a provider.

This is the RAG pipeline in one sentence: put the *right* text in front of a
model that has promised (v02) to answer only from the text in front of it.
"""

from askrepo.prompts import build_messages, format_context
from askrepo.retrieve import load_index, retrieve


def prepare(question, k=5, blend=0.7):
    """Retrieve context for the question and build the contract messages.

    Returns (messages, sources) where sources is the retrieved
    (score, chunk) list; the CLI shows it so retrieval is never a black box.
    """
    index = load_index()
    sources = retrieve(question, index, k=k, blend=blend)
    context_blocks = [
        format_context(chunk["path"], chunk["text"], start=chunk["start_line"])
        for _, chunk in sources
    ]
    return build_messages(question, context_blocks), sources
