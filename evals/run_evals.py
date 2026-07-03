"""Run the golden set against the pipeline and print the numbers.

    secrun python evals/run_evals.py                    # score a run
    secrun python evals/run_evals.py --freeze-baseline  # commit-worthy snapshot

Five metrics (the design is in ../CAPSTONE.md; the patterns come from
evals-deep-dive/evals/):

  hit@k               did retrieval surface any expected file? (pipeline-only,
                      no model needed to score it)
  citation resolve    do cited (path:line) citations point at real lines in
                      real files?
  citation match      do cited paths land in the question's expected files?
  correctness         LLM judge scores the answer against keypoints (0/0.5/1)
  decline accuracy    negative questions: did it answer with DECLINE_PHRASE?

plus cost and latency per question — quality numbers without cost numbers
are half a benchmark.

Every run is stamped with a **corpus manifest** (the HEAD SHA of each corpus
repo, plus this tool's own SHA). Separate histories mean a capstone tag can't
pin the corpus state, so the run records what it was measured against — that
is what makes baseline.run.json reproducible instead of nostalgic.

Judge caveat (from the evals dive): the judge is a model grading a model —
spot-check its verdicts by hand before trusting trends. Each question's
judge reason is saved in the run file for exactly that.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from askrepo.answer import prepare  # noqa: E402
from askrepo.config import load_config  # noqa: E402
from askrepo.prompts import DECLINE_PHRASE  # noqa: E402
from askrepo.providers import cost_usd, get_provider  # noqa: E402
from askrepo.retrieve import load_index  # noqa: E402

GOLDEN_PATH = os.path.join(HERE, "golden.jsonl")
BASELINE_PATH = os.path.join(HERE, "baseline.run.json")
RUNS_DIR = os.path.join(HERE, "runs")

CITATION = re.compile(r"\(([A-Za-z0-9_./-]+\.(?:md|py)):(\d+)(?:-(\d+))?\)")

JUDGE_SYSTEM = """\
You grade answers about a code repository. You get a question, the key points
a correct answer must contain, and a candidate answer. Score:

  1.0  — every key point is present (wording may differ)
  0.5  — some key points present, none contradicted
  0.0  — key points missing or contradicted

Judge content only; ignore style, length, and citation formatting. Reply with
ONLY a JSON object: {"score": <0 or 0.5 or 1>, "reason": "<one sentence>"}"""


def corpus_manifest(corpus_root):
    """HEAD SHA of every git repo in the corpus, plus this tool's own."""

    def sha(path):
        try:
            return subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            return None

    manifest = []
    if os.path.isdir(os.path.join(corpus_root, ".git")):
        manifest.append({"repo": ".", "sha": sha(corpus_root)})
    for name in sorted(os.listdir(corpus_root)):
        path = os.path.join(corpus_root, name)
        if os.path.isdir(os.path.join(path, ".git")) and not os.path.samefile(path, ROOT):
            manifest.append({"repo": name, "sha": sha(path)})
    manifest.append({"repo": "deep-dive-capstone (tool)", "sha": sha(ROOT)})
    return manifest


def path_matches(path, expected_files):
    return any(
        path.startswith(exp) if exp.endswith("/") else path == exp
        for exp in expected_files
    )


def score_citations(answer, expected_files, corpus_root):
    """Returns (n_citations, n_resolving, n_matching_expected)."""
    cites = CITATION.findall(answer)
    resolving = matching = 0
    for path, start, end in cites:
        full = os.path.join(corpus_root, path)
        try:
            with open(full, encoding="utf-8") as f:
                n_lines = sum(1 for _ in f)
        except OSError:
            continue  # cited file doesn't exist: resolves = no
        last = int(end) if end else int(start)
        if 1 <= int(start) <= last <= n_lines:
            resolving += 1
            if path_matches(path, expected_files):
                matching += 1
    return len(cites), resolving, matching


def judge(question, keypoints, answer, provider):
    user = (
        f"Question: {question}\n\n"
        f"Key points a correct answer must contain:\n"
        + "\n".join(f"- {k}" for k in keypoints)
        + f"\n\nCandidate answer:\n{answer}"
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]
    raw = "".join(provider.complete(messages))
    try:
        verdict = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
        return float(verdict["score"]), str(verdict.get("reason", ""))
    except (ValueError, KeyError, TypeError):
        return 0.0, f"unparseable judge output: {raw[:120]!r}"


def run(args):
    config = load_config()
    if config["PROVIDER"] == "mock":
        raise SystemExit("The mock can't be evaluated — set PROVIDER=openai or claude.")

    index = load_index()
    corpus_root = index["corpus_root"]
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = [json.loads(line) for line in f if line.strip()]

    provider = get_provider(config["PROVIDER"], model=config["MODEL"])
    judge_provider = get_provider(config["PROVIDER"], model=config["MODEL"])
    blend = float(config["BLEND"])

    results = []
    answer_cost = judge_cost = 0.0
    for q in golden:
        t0 = time.perf_counter()
        messages, sources = prepare(q["question"], k=args.k, blend=blend)
        answer = "".join(provider.complete(messages))
        latency = time.perf_counter() - t0
        cost = cost_usd(provider) or 0.0
        answer_cost += cost

        retrieved = [c["path"] for _, c in sources]
        result = {
            "id": q["id"],
            "category": q["category"],
            "retrieved": retrieved,
            "answer": answer,
            "cost_usd": round(cost, 6),
            "latency_s": round(latency, 2),
        }

        if q["answerable"]:
            result["hit"] = path_matches_any(retrieved, q["expected_files"])
            n_c, n_r, n_m = score_citations(answer, q["expected_files"], corpus_root)
            result["citations"] = {"total": n_c, "resolve": n_r, "match": n_m}
            result["judge_score"], result["judge_reason"] = judge(
                q["question"], q["keypoints"], answer, judge_provider
            )
            judge_cost += cost_usd(judge_provider) or 0.0
        else:
            result["declined"] = DECLINE_PHRASE in answer

        results.append(result)
        marker = (
            f"judge={result.get('judge_score')}"
            if q["answerable"]
            else f"declined={result.get('declined')}"
        )
        print(f"  {q['id']:<8} {marker:<14} ${cost:.4f}  {latency:.1f}s", file=sys.stderr)

    metrics = aggregate(results)
    run_data = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "mode": "rag",
        "provider": provider.name,
        "model": provider.model,
        "embed_model": index["embed_model"],
        "k": args.k,
        "blend": blend,
        "corpus_manifest": corpus_manifest(corpus_root),
        "metrics": metrics,
        "totals": {
            "answer_cost_usd": round(answer_cost, 4),
            "judge_cost_usd": round(judge_cost, 4),
        },
        "questions": results,
    }

    os.makedirs(RUNS_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(RUNS_DIR, f"{stamp}.run.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(run_data, f, indent=1)

    report(metrics, run_data["totals"])
    if args.freeze_baseline:
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(run_data, f, indent=1)
        print(f"\nbaseline frozen: {BASELINE_PATH}")
    elif os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            baseline = json.load(f)
        print("\nvs baseline "
              f"({baseline['created']}, {baseline['provider']}/{baseline['model']}):")
        for key, value in metrics.items():
            base = baseline["metrics"].get(key)
            if isinstance(value, (int, float)) and isinstance(base, (int, float)):
                print(f"  {key:<22} {base:>7.3f} -> {value:>7.3f}  ({value - base:+.3f})")
    print(f"\nrun saved: {out_path}")
    return 0


def path_matches_any(retrieved, expected_files):
    return any(path_matches(p, expected_files) for p in retrieved)


def aggregate(results):
    answerable = [r for r in results if "judge_score" in r]
    negatives = [r for r in results if "declined" in r]
    total_cites = sum(r["citations"]["total"] for r in answerable)
    metrics = {
        "hit_at_k": round(sum(r["hit"] for r in answerable) / len(answerable), 3),
        "citation_resolve": round(
            sum(r["citations"]["resolve"] for r in answerable) / total_cites, 3
        ) if total_cites else 0.0,
        "citation_match": round(
            sum(r["citations"]["match"] for r in answerable) / total_cites, 3
        ) if total_cites else 0.0,
        "judged_correctness": round(
            sum(r["judge_score"] for r in answerable) / len(answerable), 3
        ),
        "decline_accuracy": round(
            sum(r["declined"] for r in negatives) / len(negatives), 3
        ) if negatives else None,
        "mean_cost_usd": round(
            sum(r["cost_usd"] for r in results) / len(results), 6
        ),
        "mean_latency_s": round(
            sum(r["latency_s"] for r in results) / len(results), 2
        ),
        "n_questions": len(results),
        "citations_per_answer": round(total_cites / len(answerable), 1),
    }
    # per-category correctness — where the pipeline is weak matters more
    # than the overall average
    by_cat = {}
    for r in answerable:
        by_cat.setdefault(r["category"], []).append(r["judge_score"])
    metrics["correctness_by_category"] = {
        cat: round(sum(scores) / len(scores), 3) for cat, scores in sorted(by_cat.items())
    }
    return metrics


def report(metrics, totals):
    print("\n=== eval results (mode=rag) ===")
    print(f"  questions            {metrics['n_questions']}")
    print(f"  hit@k                {metrics['hit_at_k']:.3f}")
    print(f"  citation resolve     {metrics['citation_resolve']:.3f}"
          f"   ({metrics['citations_per_answer']} citations/answer)")
    print(f"  citation match       {metrics['citation_match']:.3f}")
    print(f"  judged correctness   {metrics['judged_correctness']:.3f}")
    print(f"  decline accuracy     {metrics['decline_accuracy']}")
    print(f"  mean cost / question ${metrics['mean_cost_usd']:.6f}")
    print(f"  mean latency         {metrics['mean_latency_s']}s")
    print("  correctness by category:")
    for cat, score in metrics["correctness_by_category"].items():
        print(f"    {cat:<12} {score:.3f}")
    print(f"  totals: answers ${totals['answer_cost_usd']}, "
          f"judge ${totals['judge_cost_usd']}")


def main():
    parser = argparse.ArgumentParser(description="Run the golden-set evals.")
    parser.add_argument("--k", type=int, default=5, help="retrieval depth (default 5)")
    parser.add_argument(
        "--freeze-baseline",
        action="store_true",
        help="also write this run to evals/baseline.run.json",
    )
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
