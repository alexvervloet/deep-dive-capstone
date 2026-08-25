# RAG vs agentic retrieval: measured, not asserted

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
| mean tool calls | n/a | 5.2 |

| correctness by category | rag | agent |
|---|---|---|
| code | 0.562 | 0.562 |
| concept | 0.9 | 0.6 |
| cross-dive | 0.6 | 0.4 |
| locator | 0.875 | 0.875 |

\* hit@k means different things per mode. RAG: an expected file was among the k retrieved chunks; agent: the loop grepped a hit in or read an expected file (a generous analogue; touching a file isn't proof the model used it). Compare within a column, not across.

Runs: `2026-07-03T21:33:59` (rag) · `2026-07-03T21:48:16` (agent).

## One row here is noise (added by ext-observability)

`askrepo watch` measures this repo's run-to-run wobble from the two rag runs
recorded 76 seconds apart with an identical config. On `citation match` that
wobble is **0.063**. The gap in the table above is 0.721 − 0.705 = **0.016**, so
that row is not a difference. It should be read as "the same", and the earlier
version of this file that presented it as a column comparison was overclaiming.

It gets sharper. The other rag run of the same config scored 0.784 on that
metric. Had it been the one pasted here, the row would read 0.784 vs 0.705, a
gap of 0.079, which does clear the floor. Two interchangeable runs, opposite
conclusions, and nothing but which file got opened decided it.

The verdict itself survives, and by a wide margin: judged correctness is 0.114
apart against a floor of 0.015, and hit@k 0.115 against a floor of 0.000. RAG
beats the agent here. It just does not beat it at citation matching.

Run `python -m askrepo watch` to recompute all of this.
