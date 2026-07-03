"""Turn two eval runs into the RAG-vs-agent comparison table.

    python evals/compare.py <rag.run.json> <agent.run.json> > evals/comparison.md

Reads the metrics of both runs and emits markdown. The table is the
deliverable of v05: whichever way it lands, it gets committed.
"""

import json
import sys

ROWS = [
    ("judged correctness", "judged_correctness", "{:.3f}"),
    ("retrieval hit@k*", "hit_at_k", "{:.3f}"),
    ("citation resolve", "citation_resolve", "{:.3f}"),
    ("citation match", "citation_match", "{:.3f}"),
    ("decline accuracy", "decline_accuracy", "{:.3f}"),
    ("mean cost / question", "mean_cost_usd", "${:.6f}"),
    ("mean latency", "mean_latency_s", "{:.1f}s"),
    ("mean tool calls", "mean_tool_calls", "{:.1f}"),
]


def fmt(metrics, key, pattern):
    value = metrics.get(key)
    return pattern.format(value) if value is not None else "—"


def main(rag_path, agent_path):
    with open(rag_path, encoding="utf-8") as f:
        rag = json.load(f)
    with open(agent_path, encoding="utf-8") as f:
        agent = json.load(f)
    assert rag["mode"] == "rag" and agent["mode"] == "agent"

    print("# RAG vs agentic retrieval — measured, not asserted\n")
    print(f"Same golden set ({rag['metrics']['n_questions']} questions), same model "
          f"({rag['model']}), same corpus (see the manifests in the run files). "
          f"RAG: k={rag['k']}, blend={rag['blend']}, embed={rag['embed_model']}. "
          f"Agent: grep/read_file/list_dir loop.\n")
    print("| metric | rag | agent |")
    print("|---|---|---|")
    for label, key, pattern in ROWS:
        print(f"| {label} | {fmt(rag['metrics'], key, pattern)} "
              f"| {fmt(agent['metrics'], key, pattern)} |")
    print("\n| correctness by category | rag | agent |")
    print("|---|---|---|")
    rag_cat = rag["metrics"]["correctness_by_category"]
    agent_cat = agent["metrics"]["correctness_by_category"]
    for cat in sorted(set(rag_cat) | set(agent_cat)):
        print(f"| {cat} | {rag_cat.get(cat, '—')} | {agent_cat.get(cat, '—')} |")
    print(
        "\n\\* hit@k means different things per mode — RAG: an expected file "
        "was among the k retrieved chunks; agent: the loop grepped a hit in "
        "or read an expected file (a generous analogue — touching a file "
        "isn't proof the model used it). Compare within a column, not across."
    )
    print(f"\nRuns: `{rag['created']}` (rag) · `{agent['created']}` (agent).")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
