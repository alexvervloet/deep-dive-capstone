# RAG vs agentic retrieval — measured, not asserted

Same golden set (40 questions), same model (gpt-4o-mini), same corpus (see the manifests in the run files). RAG: k=5, blend=0.7, embed=text-embedding-3-small. Agent: grep/read_file/list_dir loop.

| metric | rag | agent |
|---|---|---|
| judged correctness | 0.771 | 0.657 |
| retrieval hit@k* | 0.886 | 0.771 |
| citation resolve | 0.953 | 1.000 |
| citation match | 0.721 | 0.705 |
| decline accuracy | 1.000 | 1.000 |
| mean cost / question | $0.000407 | $0.001560 |
| mean latency | 2.7s | 9.4s |
| mean tool calls | — | 5.2 |

| correctness by category | rag | agent |
|---|---|---|
| code | 0.562 | 0.562 |
| concept | 0.9 | 0.6 |
| cross-dive | 0.6 | 0.4 |
| locator | 0.875 | 0.875 |

\* hit@k means different things per mode — RAG: an expected file was among the k retrieved chunks; agent: the loop grepped a hit in or read an expected file (a generous analogue — touching a file isn't proof the model used it). Compare within a column, not across.

Runs: `2026-07-03T21:33:59` (rag) · `2026-07-03T21:48:16` (agent).
