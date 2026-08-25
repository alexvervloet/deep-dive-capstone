"""ext-observability: watch askrepo's quality over time instead of at a moment.

v04 gave askrepo an eval: a number for how good it is *today*. v07 gave it a
trace: a record of what one request did. Neither answers the question the
Observability dive asks, which is whether the thing is still good six weeks
after you froze the baseline, and whether you would notice before someone told
you.

Porting that dive here ran into a real obstacle worth stating up front.

**askrepo is a CLI, not a service.** The dive watches six weeks of a running
system's request traffic. Nobody runs askrepo continuously, so there is no
traffic stream to trend, and generating a synthetic one would only re-teach the
dive's own simulator. What askrepo genuinely accumulates over time is different:

1. **Eval runs.** `evals/runs/*.json` and `baseline.run.json`: real runs, with
   ten metrics each, taken across different models and modes on different days.
2. **The corpus.** The baseline pins a HEAD SHA per repo. The corpus keeps
   moving underneath it.
3. **Request traces.** `ASKREPO_LOG=info` emits one JSON object per request,
   whenever you happen to run one.

So this module trends what exists. The lesson that survives the change of
subject is the dive's central one: a single number tells you nothing until you
know how much it moves on its own.
"""

from __future__ import annotations

import glob
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "evals", "runs")
BASELINE = os.path.join(ROOT, "evals", "baseline.run.json")

# The metrics worth trending, and which direction is bad. A drop in accuracy
# and a rise in cost are both regressions; a detector that only knows "changed"
# makes you read every alert to find out which.
TRENDED = {
    "hit_at_k": "down",
    "citation_resolve": "down",
    "citation_match": "down",
    "judged_correctness": "down",
    "decline_accuracy": "down",
    "mean_cost_usd": "up",
    "mean_latency_s": "up",
}


# --------------------------------------------------------------------------
# 1. The trace adapter
# --------------------------------------------------------------------------

# The Observability dive's LogRecord, which its metrics, drift, and alerting
# all take as input. Listed here as the target shape rather than imported:
# that repo is a sibling, not a dependency, and every extension in this
# capstone ports the idea rather than installing the dive.
LOG_FIELDS = (
    "ts", "trace_id", "question", "prompt_version", "model", "provider",
    "prompt_tokens", "completion_tokens", "cost_usd", "duration_ms", "cache",
    "outcome", "answer_chars",
)

# Three of the dive's fields are deliberately not above, and the reason is the
# same for each: askrepo cannot know them, and a monitor that reports a number
# it cannot know is worse than one that reports nothing.
#
#   feedback  a thumbs up/down. A CLI has no button to press.
#   segment   a cohort to slice by. One user is not a cohort.
#   answer    the answer text. Keeping it would make the log a PII sink for
#             whatever repo you pointed askrepo at, to no benefit here: the
#             judge that would grade it is the eval runner, which has the text
#             already.
UNAVAILABLE = ("feedback", "segment", "answer")

# askrepo's names on the left, the dive's on the right. Translating a name is
# an adapter's job. Inventing a *value* is not, which is why the fields askrepo
# never logged got fixed in ops.py and cli.py instead of being defaulted here.
RENAMES = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens"}


def read_traces(path):
    """Read `ASKREPO_LOG=info` output into the dive's record shape.

    Takes the `request.end` and `request.error` lines and drops the rest;
    `request.start` carries no outcome, and a half-open request is not a
    measurement. Unknown fields (`spans`, `cache_key`) are kept: a log you
    truncate on the way in is one you cannot ask new questions of later.
    """
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue  # askrepo prints its human output on stderr too
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("event") not in ("request.end", "request.error"):
                continue
            rec = {RENAMES.get(k, k): v for k, v in raw.items()}
            if raw.get("event") == "request.error":
                rec["outcome"] = "error"
            records.append(rec)
    records.sort(key=lambda r: r.get("ts", 0))
    return records


def missing_fields(records):
    """Which of the dive's fields askrepo's log still does not carry.

    Run this against a fresh log after any change to the request path; it is
    the regression test for the logging itself. Before this extension the
    answer was six fields: `prompt_version`, `model`, `provider`,
    `duration_ms`, `outcome`, and `answer_chars`. Getting to zero meant
    changing ops.py and cli.py, which was the point. See `UNAVAILABLE` for the
    three this deliberately does not ask for.
    """
    if not records:
        return list(LOG_FIELDS)
    present = set()
    for rec in records:
        present |= set(rec)
    return [f for f in LOG_FIELDS if f not in present]


# --------------------------------------------------------------------------
# 2. The eval run history
# --------------------------------------------------------------------------

def judge_of(data):
    """Which model graded this run.

    Runs written before ext-local added `JUDGE_PROVIDER` carry no judge field.
    They are not unknown: `run_evals.py` defaulted the judge to the answering
    provider, so for those runs the judge is the model in the run itself. That
    is reading what the code did, not guessing.
    """
    if data.get("judge_model"):
        return f"{data.get('judge_provider', '?')}/{data['judge_model']}"
    return f"{data.get('provider', '?')}/{data.get('model', '?')}"


def load_runs(runs_dir=RUNS_DIR, baseline=BASELINE):
    """Every recorded eval run, oldest first, with its config and judge.

    Reads `evals/runs/` *and* the loose `*.run.json` files beside it. That is
    not tidiness: `local-35b.run.json` lives at the evals root and is the only
    correctly-judged record of the 35B run, so a loader that globbed one
    directory would have silently used the wrong one.
    """
    paths = sorted(glob.glob(os.path.join(runs_dir, "*.json")))
    paths += sorted(glob.glob(os.path.join(os.path.dirname(runs_dir), "*.run.json")))
    if baseline and os.path.exists(baseline) and baseline not in paths:
        paths.append(baseline)

    runs = []
    seen = set()
    for path in paths:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        metrics = data.get("metrics", {})
        judge = judge_of(data)
        # The judge is part of a run's identity. Two files can hold the same
        # answers scored by two different graders, which is two measurements,
        # not a duplicate: that is exactly what local-35b.run.json and
        # runs/20260706-091612-rag.run.json are.
        key = (data.get("created"), data.get("mode"), data.get("model"), judge)
        if key in seen:
            continue  # baseline.run.json really is a copy of one of the runs
        seen.add(key)
        runs.append({
            "name": os.path.basename(path),
            "created": data.get("created", ""),
            "mode": data.get("mode", "?"),
            "model": data.get("model", "?"),
            "judge": judge,
            "n_questions": metrics.get("n_questions", 0),
            "config": f"{data.get('mode', '?')}/{data.get('model', '?')}",
            "metrics": metrics,
            "corpus_manifest": data.get("corpus_manifest", []),
        })
    runs.sort(key=lambda r: r["created"])
    return runs


def comparable(runs):
    """Split runs into the ones you may trend and the ones you may not.

    Three rules, all learned the boring way from this repo's own history:

    * A run over 5 questions is not a smaller version of a run over 40, it is a
      different measurement. `20260704-080140` is a 5-question spot check that
      sits in the same directory as the real runs and looks exactly like them.
    * A run whose config differs is a different experiment. Trending `qwen3:8b`
      against `gpt-4o-mini` produces a "regression" that is just a model swap.
    * A run graded by a different judge is not comparable to anything. ext-local
      already says this in prose: the judge is measurement infrastructure, not
      the system under test. `runs/20260706-091612-rag.run.json` has the 35B
      model grading its own answers and scores itself 0.771 where the constant
      gpt-4o-mini judge gave the same answers 0.786. Comparing that against a
      gpt-4o-mini-judged baseline measures the graders, not the systems.

    The full-size runs of the most-used config are the trendable set; the rest
    are returned so the report can say what it set aside rather than silently
    dropping them.
    """
    full = [r for r in runs if r["n_questions"] >= 40]
    partial = [r for r in runs if r["n_questions"] < 40]
    by_config = {}
    for run in full:
        by_config.setdefault(run["config"], []).append(run)
    if not by_config:
        return [], partial, {}
    main = max(by_config.values(), key=len)
    others = [r for r in full if r not in main]
    return main, partial + others, by_config


def same_judge(runs, reference):
    """Only the runs a reference run may legitimately be compared against."""
    if not reference:
        return []
    judge = reference[0]["judge"]
    return [r for r in runs if r["judge"] == judge]


def series(runs, metric):
    """The values of one metric across runs, skipping the ones that lack it.

    `decline_accuracy` is None on a run whose question sample happened to
    contain no negative questions. None is not zero, and averaging it in as
    zero would invent a total failure to decline.
    """
    return [
        r["metrics"][metric] for r in runs
        if isinstance(r["metrics"].get(metric), (int, float))
    ]


# --------------------------------------------------------------------------
# 3. Detectors, ported from the dive's obs/alerts.py
# --------------------------------------------------------------------------

def baseline_stats(values, robust=False):
    """Center and spread of a metric's history: what "normal" means for it.

    `robust=True` uses median and median-absolute-deviation, which the incident
    already in the history cannot drag around. The mean-and-stdev version is
    what most people reach for and is why a slow regression hides: each new bad
    day widens the band that is supposed to catch it.
    """
    if len(values) < 2:
        return (values[0] if values else 0.0), 0.0
    if robust:
        center = statistics.median(values)
        spread = statistics.median([abs(v - center) for v in values]) * 1.4826
        return center, spread
    return statistics.fmean(values), statistics.stdev(values)


def signed_z(value, center, spread):
    """How far from normal, in standard deviations, keeping the sign.

    Zero spread means every observation so far was identical, which is not
    evidence that the next one cannot differ. Returning 0.0 says "no opinion"
    rather than the infinity the arithmetic wants to give you.
    """
    if not spread:
        return 0.0
    return (value - center) / spread


def noise_floor(runs, metric):
    """How much this metric moves when *nothing changed*.

    The number that makes every other number readable, and the one nobody
    measures. Take the runs that share a config, take the spread of the metric
    across them, and that is the smallest change you are entitled to call a
    regression. Needs at least two runs of one config; returns None otherwise,
    because a noise floor guessed from one point is worse than no noise floor.
    """
    by_config = {}
    for run in runs:
        by_config.setdefault(run["config"], []).append(run)
    spreads = []
    for group in by_config.values():
        values = series(group, metric)
        if len(values) >= 2:
            spreads.append(max(values) - min(values))
    return max(spreads) if spreads else None


def detect(runs, metric, direction, robust=True):
    """Flag the latest run against the history before it.

    Two gates, and a finding has to clear both. The z-score asks whether the
    move is large relative to the metric's own history. The noise floor asks
    whether it is larger than the same config's run-to-run wobble. The second
    gate is what stops this from paging you about a model having a bad
    afternoon.
    """
    # Split the runs, not the values: `series` drops runs whose metric is None,
    # so slicing the two lists independently would eventually misalign them.
    usable = [
        r for r in runs
        if isinstance(r["metrics"].get(metric), (int, float))
    ]
    if len(usable) < 3:
        return None  # a trend needs a history, not a pair
    history_runs, values = usable[:-1], [r["metrics"][metric] for r in usable]
    *history, latest = values
    center, spread = baseline_stats(history, robust=robust)
    z = signed_z(latest, center, spread)

    # The floor comes from the history alone. Measured across all the runs it
    # would include the very point being tested, so a single spike would widen
    # the threshold to exactly cover itself and no spike could ever be caught.
    # Same trap as a baseline recomputed over the incident it is meant to find.
    floor = noise_floor(history_runs, metric)
    delta = latest - center
    moved = direction == "down" and delta < 0 or direction == "up" and delta > 0

    # A perfectly flat history has zero spread, and dividing by it gives every
    # deviation a z-score of 0. That is the worst failure an alerting system
    # can have: the more stable a metric has been, the more thoroughly it goes
    # blind the moment that metric breaks. A metric that has never moved has no
    # z-score, so the size test falls back to the noise floor. Only when both
    # are zero does the detector accept "any change at all" as the threshold,
    # and by then it has genuinely never seen this number move.
    flat = spread == 0.0
    if flat:
        big_enough = abs(delta) > floor if floor else abs(delta) > 0
    else:
        big_enough = abs(z) >= 2.0 and (floor is None or abs(delta) > floor)

    return {
        "metric": metric,
        "latest": latest,
        "center": center,
        "z": z,
        "flat_history": flat,
        "delta": abs(delta),
        "floor": floor,
        "alert": bool(moved and big_enough),
        "under_floor": floor is not None and abs(delta) <= floor,
    }


def compare_configs(runs, reference, metric):
    """Re-check every cross-config gap against the noise floor.

    This is the part that earns its keep on a repo with six runs. askrepo makes
    claims in prose: `evals/comparison.md` says RAG beats the agent,
    `comparison-local.md` compares a local model against the cloud one. Each is
    a difference between two configs, and a difference is only real if it is
    bigger than the wobble measured between two runs of the *same* config.

    So this takes the floor from the reference config and applies it to every
    gap. A verdict that clears it stands. One that does not was never a finding.
    """
    floor = noise_floor(reference, metric)
    ref_values = series(reference, metric)
    if not ref_values:
        return []
    ref_center = statistics.fmean(ref_values)

    by_config = {}
    # Cross-judge runs are dropped here, not flagged. A row saying "unknown"
    # still invites the reader to compare the numbers on it; the only safe
    # rendering of a measurement taken with a different instrument is absence.
    for run in same_judge(runs, reference):
        by_config.setdefault(run["config"], []).append(run)

    out = []
    for config, group in sorted(by_config.items()):
        if group and reference and config == reference[0]["config"]:
            continue
        values = series(group, metric)
        if not values:
            continue
        delta = statistics.fmean(values) - ref_center
        out.append({
            "config": config,
            "value": statistics.fmean(values),
            "reference": ref_center,
            "delta": delta,
            "floor": floor,
            # No floor means one run of the reference config, so nothing is
            # provable either way. That is "unknown", not "real".
            "real": None if floor is None else abs(delta) > floor,
        })
    return out


# --------------------------------------------------------------------------
# 4. Corpus drift
# --------------------------------------------------------------------------

def current_manifest(corpus_root):
    """This corpus's HEAD SHAs, using the eval runner's own manifest code.

    Imported rather than reimplemented on purpose: two functions that compute
    "the corpus state" independently will disagree eventually, and the drift
    report would be the last place you noticed.
    """
    evals_dir = os.path.join(ROOT, "evals")
    if evals_dir not in sys.path:
        sys.path.insert(0, evals_dir)
    import run_evals  # type: ignore[import-not-found]  # resolved via the sys.path insert

    return run_evals.corpus_manifest(corpus_root)


def corpus_drift(baseline_manifest, current):
    """What has changed in the corpus since the baseline was frozen.

    The v04 design note says the manifest exists so runs are "reproducible
    against any corpus rather than only this one". True, and it buys a second
    thing nobody planned for: it makes the baseline's own staleness
    *measurable*. A frozen number is only meaningful next to the corpus it was
    measured on, and this says whether that corpus still exists.
    """
    was = {r["repo"]: r["sha"] for r in baseline_manifest}
    now = {r["repo"]: r["sha"] for r in current}
    added = sorted(set(now) - set(was))
    removed = sorted(set(was) - set(now))
    moved = sorted(r for r in set(was) & set(now) if was[r] != now[r])
    unchanged = sorted(r for r in set(was) & set(now) if was[r] == now[r])
    return {
        "pinned": len(was),
        "now": len(now),
        "added": added,
        "removed": removed,
        "moved": moved,
        "unchanged": unchanged,
        # Any move at all means the measured corpus is gone. The count is for
        # how far gone, not whether.
        "stale": bool(added or removed or moved),
    }


# --------------------------------------------------------------------------
# 5. The report
# --------------------------------------------------------------------------

# A cheap question costs $0.0004. Printed to three decimals that is "0.000",
# which reads as free and hides the one metric most likely to creep.
PLACES = {"mean_cost_usd": 6, "mean_latency_s": 2}


def _fmt_float(value, metric=None, places=3, sign=False):
    if value is None:
        return "n/a"
    spec = f"{'+' if sign else ''}.{PLACES.get(metric, places)}f"
    return format(value, spec)


def report(corpus_root, log_path=None, metric_set=None):
    """The `askrepo watch` output: corpus staleness, then quality over time.

    Ordered by what invalidates what. A baseline measured on a corpus that no
    longer exists is not a baseline, so the drift check comes first; there is
    no point reading a trend whose reference point has already expired.
    """
    lines = []
    metrics = metric_set or list(TRENDED)
    runs = load_runs()

    # --- corpus
    lines.append("corpus")
    if not runs:
        lines.append("  no eval runs on file; nothing pins a corpus state")
    else:
        pinned = runs[0]["corpus_manifest"]
        drift = corpus_drift(pinned, current_manifest(corpus_root))
        lines.append(f"  baseline frozen  {runs[0]['created']} ({runs[0]['name']})")
        lines.append(f"  repos pinned     {drift['pinned']}")
        lines.append(f"  repos now        {drift['now']}")
        if drift["added"]:
            label = f"added ({len(drift['added'])})"
            lines.append(f"  {label:<16} "
                         + ", ".join(drift["added"][:4])
                         + (", ..." if len(drift["added"]) > 4 else ""))
        if drift["removed"]:
            label = f"removed ({len(drift['removed'])})"
            lines.append(f"  {label:<16} " + ", ".join(drift["removed"]))
        lines.append(f"  moved            {len(drift['moved'])} of "
                     f"{len(drift['moved']) + len(drift['unchanged'])} still-present repos")
        if drift["stale"]:
            lines.append("  verdict          STALE. The baseline's numbers were measured "
                         "on a corpus that no longer exists.")
            lines.append("                   Re-freeze before comparing a new run to it.")
        else:
            lines.append("  verdict          current")

    # --- runs
    lines.append("")
    lines.append("quality")
    if not runs:
        return "\n".join(lines)

    main, aside, _ = comparable(runs)
    lines.append(f"  runs on file     {len(runs)} "
                 f"({runs[0]['created'][:10]} to {runs[-1]['created'][:10]})")
    if not main:
        lines.append("  no full-size runs; nothing to trend")
        return "\n".join(lines)

    lines.append(f"  trendable        {len(main)} of config {main[0]['config']}")
    lines.append(f"  judged by        {main[0]['judge']}")
    for run in aside:
        if run["n_questions"] < 40:
            why = "partial run, n=%d" % run["n_questions"]
        elif run["judge"] != main[0]["judge"]:
            why = f"judged by {run['judge']}, not comparable"
        else:
            why = "different config"
        lines.append(f"  set aside        {run['name']}  ({why})")

    # --- noise floor
    lines.append("")
    lines.append("  noise floor (spread across runs of one config, nothing changed)")
    for metric in metrics:
        floor = noise_floor(main, metric)
        note = ""
        if floor == 0.0:
            note = "  <- deterministic here; any gap counts, so read it with care"
        elif floor is None:
            note = "  <- needs two runs of one config"
        lines.append(f"    {metric:<20} {_fmt_float(floor, metric)}{note}")
    if len(main) < 2:
        lines.append("    (one run of the main config: no floor is measurable yet)")

    # --- alerting
    lines.append("")
    lines.append("  alerts on the latest run")
    fired = []
    for metric in metrics:
        found = detect(main, metric, TRENDED.get(metric, "down"))
        if found and found["alert"]:
            fired.append(found)
    if fired:
        for found in fired:
            lines.append(f"    {found['metric']:<20} {found['latest']:.3f} "
                         f"({found['z']:+.1f} sigma vs {found['center']:.3f})")
    else:
        lines.append(f"    none. {len(main)} run(s) of {main[0]['config']} is below the "
                     "3 a trend needs,")
        lines.append("    so this says 'cannot tell', not 'all clear'.")

    # --- cross-config
    lines.append("")
    lines.append(f"  gaps vs {main[0]['config']}, checked against the floor")
    for metric in metrics:
        rows = compare_configs(runs, main, metric)
        if not rows:
            continue
        lines.append(f"    {metric}")
        for row in rows:
            verdict = {True: "real", False: "NOISE", None: "unknown"}[row["real"]]
            lines.append(
                f"      {row['config']:<24} "
                f"{_fmt_float(row['value'], metric):>9}  "
                f"{_fmt_float(row['delta'], metric, sign=True):>10}  {verdict}")

    # --- traces
    if log_path and os.path.exists(log_path):
        lines.append("")
        lines.append(f"traces ({log_path})")
        records = read_traces(log_path)
        lines.append(f"  requests         {len(records)}")
        if records:
            outcomes = {}
            for rec in records:
                outcomes[rec.get("outcome", "unknown")] = \
                    outcomes.get(rec.get("outcome", "unknown"), 0) + 1
            lines.append("  outcomes         "
                         + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))
            costs = [r.get("cost_usd", 0.0) for r in records]
            durations = [r.get("duration_ms", 0.0) for r in records]
            hits = sum(1 for r in records if r.get("cache") == "hit")
            lines.append(f"  total cost       ${sum(costs):.6f}")
            lines.append(f"  mean latency     {statistics.fmean(durations):.1f} ms")
            lines.append(f"  cache hit rate   {hits / len(records):.2f}")
        gaps = missing_fields(records)
        if gaps:
            lines.append("  fields still missing vs the dive's record shape: "
                         + ", ".join(gaps))

    return "\n".join(lines)
