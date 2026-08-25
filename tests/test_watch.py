"""Tests for the quality watch (feat/observability): offline, no key, no network.

Two things matter here, and they are different in kind. The adapter has to
translate askrepo's log without inventing anything, and the detectors have to
refuse to call a difference real when it is inside the measured noise. The
second is the one worth having tests for: a detector that fires is easy, and a
detector that stays quiet for the right reason is the whole feature.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo.watch import (  # noqa: E402
    LOG_FIELDS, baseline_stats, comparable, compare_configs, corpus_drift,
    detect, judge_of, load_runs, missing_fields, noise_floor, read_traces,
    report, same_judge, series, signed_z,
)


def _run(created, mode, model, n=40, judge="openai/gpt-4o-mini", **metrics):
    """One eval run in the shape load_runs returns."""
    return {
        "name": f"{created}.run.json",
        "created": created,
        "mode": mode,
        "model": model,
        "judge": judge,
        "n_questions": n,
        "config": f"{mode}/{model}",
        "metrics": {"n_questions": n, **metrics},
        "corpus_manifest": [],
    }


class TestTraceAdapter(unittest.TestCase):
    def _log(self, *lines):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for line in lines:
                f.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def test_keeps_only_finished_requests(self):
        path = self._log(
            {"event": "request.start", "trace_id": "a", "ts": 1.0},
            {"event": "request.end", "trace_id": "a", "ts": 2.0},
        )
        records = read_traces(path)
        self.assertEqual(len(records), 1)  # a half-open request is not a measurement
        self.assertEqual(records[0]["trace_id"], "a")

    def test_renames_token_fields_without_inventing_them(self):
        path = self._log({"event": "request.end", "input_tokens": 11,
                          "output_tokens": 3, "ts": 1.0})
        rec = read_traces(path)[0]
        self.assertEqual(rec["prompt_tokens"], 11)
        self.assertEqual(rec["completion_tokens"], 3)
        self.assertNotIn("input_tokens", rec)

    def test_error_lines_become_the_error_outcome(self):
        path = self._log({"event": "request.error", "error": "BudgetExceeded", "ts": 1.0})
        self.assertEqual(read_traces(path)[0]["outcome"], "error")

    def test_survives_the_human_output_on_the_same_stream(self):
        # askrepo prints "cost: $0.00" and "retrieved: ..." to stderr too, so a
        # redirected log is a mix of JSON and prose. Choking on that would make
        # the adapter useless against a real capture.
        path = self._log(
            "provider: mock (canned-answer)",
            {"event": "request.end", "ts": 1.0, "trace_id": "a"},
            "cost: $0.000000 (0 in / 0 out)",
            "{not valid json",
        )
        self.assertEqual(len(read_traces(path)), 1)

    def test_sorts_by_timestamp(self):
        path = self._log(
            {"event": "request.end", "trace_id": "b", "ts": 9.0},
            {"event": "request.end", "trace_id": "a", "ts": 1.0},
        )
        self.assertEqual([r["trace_id"] for r in read_traces(path)], ["a", "b"])

    def test_missing_fields_names_every_gap_on_an_empty_log(self):
        self.assertEqual(missing_fields([]), list(LOG_FIELDS))

    def test_a_complete_record_has_no_gaps(self):
        rec = {f: 1 for f in LOG_FIELDS}
        self.assertEqual(missing_fields([rec]), [])


class TestRunHistory(unittest.TestCase):
    def test_partial_runs_are_set_aside(self):
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", n=40, hit_at_k=0.9),
            _run("2026-07-03T11:00:00", "rag", "gpt", n=5, hit_at_k=1.0),
        ]
        main, aside, _ = comparable(runs)
        self.assertEqual(len(main), 1)
        self.assertEqual(aside[0]["n_questions"], 5)

    def test_other_configs_are_set_aside_not_trended(self):
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", hit_at_k=0.9),
            _run("2026-07-03T11:00:00", "rag", "gpt", hit_at_k=0.9),
            _run("2026-07-03T12:00:00", "agent", "gpt", hit_at_k=0.7),
        ]
        main, aside, _ = comparable(runs)
        self.assertEqual({r["config"] for r in main}, {"rag/gpt"})
        self.assertEqual([r["config"] for r in aside], ["agent/gpt"])

    def test_series_skips_null_metrics_rather_than_zeroing_them(self):
        # decline_accuracy is None when a run drew no negative questions.
        # Averaging that in as 0.0 would invent a total failure to decline.
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", decline_accuracy=1.0),
            _run("2026-07-03T11:00:00", "rag", "gpt", decline_accuracy=None),
        ]
        self.assertEqual(series(runs, "decline_accuracy"), [1.0])


class TestJudge(unittest.TestCase):
    def test_a_missing_judge_field_means_the_answerer_graded_itself(self):
        # Runs predating ext-local's JUDGE_PROVIDER carry no judge field, and
        # run_evals defaulted the judge to the answering provider. Reading that
        # off the run is recovering what the code did, not guessing.
        self.assertEqual(
            judge_of({"provider": "openai", "model": "gpt-4o-mini"}),
            "openai/gpt-4o-mini",
        )

    def test_an_explicit_judge_wins(self):
        self.assertEqual(
            judge_of({"provider": "local", "model": "qwen3:8b",
                      "judge_provider": "openai", "judge_model": "gpt-4o-mini"}),
            "openai/gpt-4o-mini",
        )

    def test_a_differently_judged_run_is_dropped_not_flagged(self):
        # The 35B model grading its own answers scored 0.771 where the constant
        # gpt-4o-mini judge gave the same answers 0.786. Showing that row with
        # a caveat still invites the comparison; it has to be absent.
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", judged_correctness=0.786),
            _run("2026-07-03T11:00:00", "rag", "gpt", judged_correctness=0.771),
            _run("2026-07-06T09:00:00", "rag", "qwen", judge="local/qwen",
                 judged_correctness=0.771),
        ]
        main, _, _ = comparable(runs)
        self.assertEqual(compare_configs(runs, main, "judged_correctness"), [])
        self.assertEqual(len(same_judge(runs, main)), 2)


class TestDetectors(unittest.TestCase):
    def test_zero_spread_yields_no_opinion_not_infinity(self):
        self.assertEqual(signed_z(0.5, 0.9, 0.0), 0.0)

    def test_robust_stats_ignore_the_incident_in_the_history(self):
        values = [0.9, 0.9, 0.9, 0.9, 0.1]  # one bad day
        mean_center, mean_spread = baseline_stats(values)
        med_center, med_spread = baseline_stats(values, robust=True)
        self.assertLess(mean_center, med_center)   # the outlier drags the mean
        self.assertGreater(mean_spread, med_spread)  # and widens the band

    def test_noise_floor_needs_two_runs_of_one_config(self):
        one = [_run("2026-07-03T10:00:00", "rag", "gpt", hit_at_k=0.9)]
        self.assertIsNone(noise_floor(one, "hit_at_k"))

    def test_noise_floor_is_the_spread_when_nothing_changed(self):
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", citation_match=0.784),
            _run("2026-07-03T11:00:00", "rag", "gpt", citation_match=0.721),
        ]
        self.assertAlmostEqual(noise_floor(runs, "citation_match"), 0.063, places=3)

    def test_a_trend_needs_more_than_a_pair(self):
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", hit_at_k=0.9),
            _run("2026-07-03T11:00:00", "rag", "gpt", hit_at_k=0.5),
        ]
        self.assertIsNone(detect(runs, "hit_at_k", "down"))

    def test_a_drop_inside_the_noise_floor_does_not_alert(self):
        # Four steady runs, then one a little lower. The z-score alone would
        # call it, because four identical runs make any deviation look huge.
        # The floor is what keeps it quiet.
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", citation_match=0.78),
            _run("2026-07-03T11:00:00", "rag", "gpt", citation_match=0.72),
            _run("2026-07-03T12:00:00", "rag", "gpt", citation_match=0.75),
            _run("2026-07-03T13:00:00", "rag", "gpt", citation_match=0.74),
            _run("2026-07-03T14:00:00", "rag", "gpt", citation_match=0.73),
        ]
        found = detect(runs, "citation_match", "down")
        self.assertTrue(found["under_floor"])
        self.assertFalse(found["alert"])

    def test_the_spike_does_not_get_to_widen_its_own_threshold(self):
        # The first version of detect() measured the floor across every run,
        # including the one under test. A 100x cost spike then set a floor of
        # exactly its own size and cancelled itself out, so the detector was
        # silent precisely when it mattered. The floor has to come from the
        # history alone.
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", mean_cost_usd=0.0004),
            _run("2026-07-03T11:00:00", "rag", "gpt", mean_cost_usd=0.0004),
            _run("2026-07-03T12:00:00", "rag", "gpt", mean_cost_usd=0.0004),
            _run("2026-07-03T13:00:00", "rag", "gpt", mean_cost_usd=0.0400),
        ]
        self.assertEqual(noise_floor(runs, "mean_cost_usd"), 0.0396)  # with it
        self.assertEqual(noise_floor(runs[:-1], "mean_cost_usd"), 0.0)  # without
        self.assertTrue(detect(runs, "mean_cost_usd", "up")["alert"])

    def test_a_rise_in_cost_is_a_regression_too(self):
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", mean_cost_usd=0.0004),
            _run("2026-07-03T11:00:00", "rag", "gpt", mean_cost_usd=0.0004),
            _run("2026-07-03T12:00:00", "rag", "gpt", mean_cost_usd=0.0004),
            _run("2026-07-03T13:00:00", "rag", "gpt", mean_cost_usd=0.0400),
        ]
        found = detect(runs, "mean_cost_usd", "up")
        self.assertTrue(found["alert"])
        # ...and the same series read as "down" is not an alert: direction is
        # what turns a change into a regression.
        self.assertFalse(detect(runs, "mean_cost_usd", "down")["alert"])


class TestCrossConfig(unittest.TestCase):
    def _runs(self):
        return [
            _run("2026-07-03T10:00:00", "rag", "gpt", citation_match=0.784,
                 judged_correctness=0.786),
            _run("2026-07-03T11:00:00", "rag", "gpt", citation_match=0.721,
                 judged_correctness=0.771),
            _run("2026-07-03T12:00:00", "agent", "gpt", citation_match=0.705,
                 judged_correctness=0.657),
        ]
    def test_a_gap_smaller_than_the_floor_is_not_a_finding(self):
        runs = self._runs()
        main, _, _ = comparable(runs)
        row = compare_configs(runs, main, "citation_match")[0]
        self.assertEqual(row["config"], "agent/gpt")
        self.assertFalse(row["real"])  # 0.047 apart, inside a 0.063 floor

    def test_a_gap_larger_than_the_floor_stands(self):
        runs = self._runs()
        main, _, _ = comparable(runs)
        row = compare_configs(runs, main, "judged_correctness")[0]
        self.assertTrue(row["real"])  # 0.12 apart, floor is 0.015

    def test_without_a_floor_the_verdict_is_unknown_not_real(self):
        runs = [
            _run("2026-07-03T10:00:00", "rag", "gpt", judged_correctness=0.786),
            _run("2026-07-03T12:00:00", "agent", "gpt", judged_correctness=0.657),
        ]
        main, _, _ = comparable(runs)
        row = compare_configs(runs, main, "judged_correctness")[0]
        self.assertIsNone(row["real"])


class TestCorpusDrift(unittest.TestCase):
    def test_a_moved_sha_makes_the_baseline_stale(self):
        was = [{"repo": "rag-deep-dive", "sha": "aaa"}]
        now = [{"repo": "rag-deep-dive", "sha": "bbb"}]
        drift = corpus_drift(was, now)
        self.assertEqual(drift["moved"], ["rag-deep-dive"])
        self.assertTrue(drift["stale"])

    def test_a_new_repo_counts_as_drift(self):
        was = [{"repo": "rag-deep-dive", "sha": "aaa"}]
        now = [{"repo": "rag-deep-dive", "sha": "aaa"},
               {"repo": "architecture-deep-dive", "sha": "ccc"}]
        drift = corpus_drift(was, now)
        self.assertEqual(drift["added"], ["architecture-deep-dive"])
        self.assertEqual(drift["moved"], [])
        self.assertTrue(drift["stale"])  # the corpus grew under the baseline

    def test_an_untouched_corpus_is_not_stale(self):
        same = [{"repo": "rag-deep-dive", "sha": "aaa"}]
        drift = corpus_drift(same, list(same))
        self.assertFalse(drift["stale"])
        self.assertEqual(drift["unchanged"], ["rag-deep-dive"])


class TestReport(unittest.TestCase):
    def test_renders_against_this_repos_real_runs(self):
        # The one test that touches the real evals/ directory. It asserts the
        # report renders and reaches its verdict, not the numbers themselves:
        # those change the next time anyone freezes a baseline, and a test that
        # pins them would just be a second copy of the run files.
        runs = load_runs()
        self.assertTrue(runs, "evals/runs should not be empty")
        text = report(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.assertIn("corpus", text)
        self.assertIn("noise floor", text)
        self.assertIn("gaps vs", text)


if __name__ == "__main__":
    unittest.main()
