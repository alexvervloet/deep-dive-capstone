# Lessons

What did not go according to plan while building askrepo, recorded when it
happened rather than reconstructed afterwards.

## 2026-08-25: The alerting went blind on exactly the metrics that mattered

**Expected.** Port the Observability dive's z-score detector, point it at the
eval runs, done. It is forty lines of statistics.

**What happened.** Two bugs, both of which made the detector quiet in the one
situation you build a detector for.

The first: a metric with a perfectly flat history has zero standard deviation,
and `signed_z` returns 0.0 rather than dividing by it. So three identical runs
followed by a 100x cost spike scored z=0 and did not alert. The more stable a
metric had been, the more completely the detector ignored it breaking. That is
backwards from how monitoring is supposed to behave and it would have shipped,
because the report against this repo's real runs looked entirely sensible.

The second was worse and hid behind the first. The noise floor was measured
across every run of a config, including the run being tested. A spike therefore
widened the floor to exactly its own size and cancelled itself out. Two
independent gates, and the incident defeated both by being the incident.

**Next time.** A detector's baseline must never include the point it is judging.
Obvious written down; not obvious while writing the list comprehension that
computes it over `runs`. And test the *silence*. Both bugs were found by a test
asserting an alert fires, which is the test people skip because it feels like
testing arithmetic. The tests that pass trivially are `test_a_drop_inside_the_
noise_floor_does_not_alert`; the one that caught real bugs was its opposite.

## 2026-08-25: "The same shape" was six fields short

**Expected.** The Observability dive says its `LogRecord` is deliberately the
same shape Production's `trace.summary()` emits, and askrepo's v07 ops layer is
built from that Production dive. So the trace log would load into the dive's
analysis directly, and the adapter would be a rename or two.

**What happened.** Six required fields were missing: `prompt_version`, `model`,
`provider`, `duration_ms`, `outcome`, and `answer_chars`. v07 timed each span
but never the request, and set the provider deep inside `_produce`, so the two
early exits in `cmd_ask` (cache hit, budget block) logged a request with no
model on it at all. Every one of those is invisible until something tries to
consume the log, because the log looked fine to a human reading one line of it.

**Next time.** "Compatible shape" between two repos is a claim, not a fact, and
the cheapest way to check it is to write the consumer. The fix belonged in
`ops.py` and `cli.py` rather than in the adapter: an adapter that defaults a
missing field is how a dashboard ends up confidently reporting a number nobody
ever measured. `watch.missing_fields()` exists so the next person can re-run
that check in one line instead of rediscovering it.

## 2026-08-25: Two files, one run, two different scores

**Expected.** `evals/runs/` holds the runs. Glob it, sort by date, trend.

**What happened.** `evals/local-35b.run.json` sits at the evals root rather than
in `runs/`, and `runs/20260706-091612-rag.run.json` has the same `created`
timestamp and the same model. They are the same answers scored twice: once by
the constant `gpt-4o-mini` judge (0.786) and once by the 35B model grading
itself (0.771). A loader that globbed one directory silently picked the
self-judged one, and the first working version of the cross-config table
compared a qwen-judged score against a gpt-4o-mini-judged baseline. That
measures the graders.

ext-local had already written the rule down in the README: the judge is
measurement infrastructure, not the system under test, so it must stay constant
across runs you compare. The rule was stated, then broken by a directory layout.

**Next time.** A rule that lives only in prose gets broken by the next tool that
reads the data. `watch.py` now treats the judge as part of a run's identity, and
drops cross-judge runs from comparisons rather than showing them with a caveat,
because a caveat still leaves two numbers side by side for someone to subtract.
Runs from before the `JUDGE_PROVIDER` override carry no judge field at all;
those are recoverable, since `run_evals.py` defaulted the judge to the answering
provider, but only if you go read what the code did at the time.

## 2026-08-25: A CLI does not have production traffic

**Expected.** ext-observability would watch askrepo's request stream the way the
dive watches its support assistant's, and the extension would be mostly the
dive's machinery pointed at a new log.

**What happened.** askrepo is a local CLI. It has no continuous traffic, so
there is nothing to trend, and generating synthetic traffic would only re-teach
the simulator the dive already ships. What askrepo actually accumulates is eval
runs, a corpus that moves underneath a frozen baseline, and occasional traces.

Retargeting at those turned up something better than the original plan. Two of
the six recorded runs share a config and sit 76 seconds apart, which makes them
a free measurement of run-to-run noise: 6.3 points on `citation_match`, 1.5 on
`judged_correctness`, and exactly 0.0 on `hit_at_k`, because retrieval is
deterministic and only the generated text is not. That noise floor then
invalidated a row in `evals/comparison.md` that had been sitting there since
v05.

**Next time.** When a dive's subject does not exist in the target, look for what
the target accumulates instead of manufacturing the subject. Also: two runs of
an unchanged config are worth more than a third experiment, and nobody ever
schedules them.
