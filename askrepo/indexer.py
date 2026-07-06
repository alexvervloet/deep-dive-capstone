"""Build the index: walk a corpus, chunk it, embed it, save it.

Two chunkers, both structure-aware and both line-tracking (adapted from
rag-deep-dive/rag/chunking.py, which explains the tradeoffs):

  - Markdown splits at headings — each section is about one thing, and the
    heading travels with the chunk.
  - Python splits at top-level `def`/`class` boundaries — one object per
    chunk, docstring included.

Every chunk carries (path, start_line, end_line) so answers can cite
(path:line) and the citation resolves to a real place in a real file. That's
the whole reason these chunkers track lines instead of reusing the word-window
splitter from the RAG dive.

The saved index is one JSON file — human-readable on purpose, so you can peek
inside (the RAG dive's store.py makes the same call). Vectors are rounded to
6 decimals to keep it a reasonable size; production systems use binary
formats or a vector database, which changes the container, not the idea.
"""

import datetime
import json
import os
import re

from askrepo.providers import EMBED_MODELS, EMBED_PRICES, embed

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ASKREPO_INDEX lets a local-embedded index live beside the cloud one instead
# of overwriting it (ext-local) — so the eval comparison keeps both stacks'
# indexes on disk at once:  ASKREPO_INDEX=index/index.local.json askrepo index ..
INDEX_PATH = os.getenv("ASKREPO_INDEX") or os.path.join(HERE, "index", "index.json")

INDEXED_EXTENSIONS = {".md", ".py"}
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".history", ".vscode",
    "node_modules", "index", ".idea",
}
MAX_CHUNK_LINES = 60  # size-cap for a single section/object
OVERLAP_LINES = 10    # window overlap when a long section gets split
EMBED_BATCH = 100     # texts per embedding request (both stacks allow this)

_HEADING = re.compile(r"^#{1,6}\s")
_PY_BOUNDARY = re.compile(r"^(def |class |@)")  # top-level objects (col 0)


def _windows(lines, start_line):
    """Split an oversized run of lines into overlapping windows."""
    step = MAX_CHUNK_LINES - OVERLAP_LINES
    out = []
    for offset in range(0, len(lines), step):
        window = lines[offset : offset + MAX_CHUNK_LINES]
        out.append((start_line + offset, window))
        if offset + MAX_CHUNK_LINES >= len(lines):
            break
    return out


def _emit(chunks, path, start_line, lines):
    text = "\n".join(lines).strip("\n")
    if not text.strip():
        return
    for win_start, win_lines in _windows(lines, start_line):
        win_text = "\n".join(win_lines).strip("\n")
        if win_text.strip():
            chunks.append({
                "path": path,
                "start_line": win_start,
                "end_line": win_start + len(win_lines) - 1,
                "text": win_text,
            })


def chunk_markdown(path, text):
    """One chunk per heading section (heading line included), size-capped."""
    chunks = []
    lines = text.splitlines()
    section_start, buf = 1, []
    for i, line in enumerate(lines, start=1):
        if _HEADING.match(line) and buf:
            _emit(chunks, path, section_start, buf)
            section_start, buf = i, [line]
        else:
            buf.append(line)
    _emit(chunks, path, section_start, buf)
    return chunks


def chunk_python(path, text):
    """One chunk per top-level def/class (decorators ride along), size-capped.

    Everything before the first boundary — module docstring, imports,
    constants — becomes the header chunk, which is often the most citable
    part of a teaching file.
    """
    chunks = []
    lines = text.splitlines()
    object_start, buf = 1, []
    for i, line in enumerate(lines, start=1):
        starts_object = _PY_BOUNDARY.match(line) and not (
            buf and _PY_BOUNDARY.match(buf[-1])  # decorator stack stays glued
        )
        if starts_object and buf:
            _emit(chunks, path, object_start, buf)
            object_start, buf = i, [line]
        else:
            buf.append(line)
    _emit(chunks, path, object_start, buf)
    return chunks


def collect_chunks(corpus_root):
    """Walk the corpus and chunk every indexable file. No API calls."""
    corpus_root = os.path.abspath(corpus_root)
    chunks = []
    n_files = 0
    def keep(dirpath, d):
        # skip junk dirs — and the capstone itself, so the corpus is the
        # series, not this tool (the self-indexing decision from CAPSTONE.md)
        if d in SKIP_DIRS:
            return False
        return not os.path.samefile(os.path.join(dirpath, d), HERE)

    for dirpath, dirnames, filenames in os.walk(corpus_root):
        dirnames[:] = sorted(d for d in dirnames if keep(dirpath, d))
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1]
            if ext not in INDEXED_EXTENSIONS:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, corpus_root)
            try:
                with open(full, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            chunker = chunk_markdown if ext == ".md" else chunk_python
            chunks.extend(chunker(rel, text))
            n_files += 1
    return chunks, n_files


def build_index(corpus_root, stack):
    """Chunk the corpus, embed every chunk, save index/index.json.

    Returns (n_files, n_chunks, total_tokens, cost_usd).
    """
    chunks, n_files = collect_chunks(corpus_root)
    if not chunks:
        raise SystemExit(f"No .md or .py files found under {corpus_root!r}.")

    total_tokens = 0
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        vectors, tokens = embed([c["text"] for c in batch], stack)
        total_tokens += tokens
        for chunk, vector in zip(batch, vectors):
            chunk["vector"] = [round(x, 6) for x in vector]

    embed_model = EMBED_MODELS[stack]
    index = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "corpus_root": os.path.abspath(corpus_root),
        "stack": stack,
        "embed_model": embed_model,
        "embed_tokens": total_tokens,
        "chunks": chunks,
    }
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f)

    # .get(): any local embed model is free (it's your hardware), whatever it's
    # named — so a runner-of-choice embed model never KeyErrors the price table.
    cost = total_tokens * EMBED_PRICES.get(embed_model, 0.0) / 1_000_000
    return n_files, len(chunks), total_tokens, cost
