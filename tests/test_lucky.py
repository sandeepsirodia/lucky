"""Tests map 1:1 to SPEC.md (E1..E13). Reference values are computed independently
(brute-force enumeration, published tables), never by the code under test."""
import io
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lucky  # noqa: E402

PY = sys.executable


def cli(*argv):
    out = io.StringIO()
    code = lucky.main(list(argv), out=out)
    return code, out.getvalue()


def brute_fisher(a, b, c, d):
    """Independent reference: enumerate every table with the same margins using exact integers."""
    n, r1, c1 = a + b + c + d, a + b, a + c
    prob = lambda x: math.comb(r1, x) * math.comb(n - r1, c1 - x)  # noqa: E731  (unnormalised, exact)
    obs, total = prob(a), math.comb(n, c1)
    return sum(prob(x) for x in range(max(0, c1 - (n - r1)), min(r1, c1) + 1) if prob(x) <= obs) / total


class TestStats(unittest.TestCase):
    def test_e1_wilson_reference(self):
        lo, hi = lucky.wilson(17, 20)
        self.assertEqual((round(lo, 3), round(hi, 3)), (0.640, 0.948))
        lo, hi = lucky.wilson(50, 100)  # textbook: 0.404–0.596
        self.assertEqual((round(lo, 3), round(hi, 3)), (0.404, 0.596))

    def test_e2_wilson_edges(self):
        self.assertEqual(lucky.wilson(0, 20)[0], 0.0)
        self.assertEqual(lucky.wilson(20, 20)[1], 1.0)
        for k in range(21):
            lo, hi = lucky.wilson(k, 20)
            self.assertTrue(0 <= lo <= k / 20 <= hi <= 1)

    def test_e3_fisher_matches_brute_force(self):
        self.assertAlmostEqual(lucky.fisher_exact(17, 3, 12, 8), 0.1552, places=4)
        rng = random.Random(1)
        for _ in range(200):
            a, b, c, d = (rng.randint(0, 15) for _ in range(4))
            if a + b + c + d == 0:
                continue
            self.assertAlmostEqual(lucky.fisher_exact(a, b, c, d), brute_fisher(a, b, c, d), places=9)
        _, out = cli("-n", "1", "true")  # sanity that CLI works at all
        pairs = lucky.compare([self._t(17, 20), self._t(12, 20)])
        self.assertIn("No detectable difference", pairs[0]["verdict"])

    def test_e4_real_difference(self):
        pairs = lucky.compare([self._t(45, 50), self._t(20, 50)])
        self.assertTrue(pairs[0]["significant"])
        self.assertLess(pairs[0]["p"], 0.001)
        self.assertIn("#1 is better", pairs[0]["verdict"])

    def test_e9_sample_size_formula(self):
        p1, p2 = 0.85, 0.60
        za, zb = 1.959964, 0.841621
        pbar = (p1 + p2) / 2
        n = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / (p1 - p2) ** 2
        ref = n / 4 * (1 + math.sqrt(1 + 4 / (n * abs(p1 - p2)))) ** 2  # Fleiss (1981) continuity correction
        self.assertLessEqual(abs(lucky.sample_size(p1, p2) - ref) / ref, 0.10)
        self.assertIsNone(lucky.sample_size(0.5, 0.5))

    def test_e9b_sample_size_is_honest(self):
        # Simulate: at the suggested n, a real 0.85 vs 0.60 gap should be detected ~80% of the time.
        rng, n = random.Random(3), lucky.sample_size(0.85, 0.60)
        hits = 0
        for _ in range(400):
            a = sum(rng.random() < 0.85 for _ in range(n))
            b = sum(rng.random() < 0.60 for _ in range(n))
            hits += lucky.fisher_exact(a, n - a, b, n - b) < 0.05
        self.assertGreaterEqual(hits / 400, 0.72)

    def test_never_suggests_fewer_runs_than_done(self):
        pairs = lucky.compare([self._t(2, 6), self._t(6, 6)])
        self.assertFalse(pairs[0]["significant"])
        self.assertGreater(pairs[0]["n_needed"], 6)

    def test_e11_holm(self):
        # Reference: p = [0.01, 0.04, 0.03] -> Holm = [0.03, 0.06, 0.06]
        self.assertEqual([round(x, 6) for x in lucky.holm([0.01, 0.04, 0.03])], [0.03, 0.06, 0.06])

    def test_e8_early_stopping_controls_false_positives(self):
        rng = random.Random(7)
        coin = lambda: int(rng.random() < 0.7)  # noqa: E731  identical coins: every "difference" is false
        trials = 2000
        fp = sum(lucky.sequential_compare(coin, coin, max_n=100, looks=5)[3] for _ in range(trials))
        rate = fp / trials
        self.assertLessEqual(rate, 0.05 + 2 * math.sqrt(0.05 * 0.95 / trials))

    def test_e8b_early_stopping_still_has_power(self):
        rng = random.Random(8)
        hits = sum(lucky.sequential_compare(lambda: int(rng.random() < 0.9), lambda: int(rng.random() < 0.4),
                                            max_n=60, looks=5)[3] for _ in range(300))
        self.assertGreater(hits / 300, 0.9)

    @staticmethod
    def _t(k, n):
        t = lucky.Tally("x")
        t.passes, t.fails = k, n - k
        return t


class TestCLI(unittest.TestCase):
    def test_e5_stable_pass(self):
        code, out = cli("-n", "10", "true")
        self.assertIn("10/10 passed", out)
        self.assertIn("stable pass", out)

    def test_e6_flaky(self):
        cmd = '%s -c "import os,sys; sys.exit(int(os.environ[\'LUCKY_RUN\']) %% 2)"' % PY
        code, out = cli("-n", "20", cmd)
        self.assertIn("10/20 passed", out)
        self.assertIn("flaky (10/20 passed)", out)

    def test_e7_timeout_kills_children(self):
        marker = "31.%d" % os.getpid()
        code, out = cli("-n", "2", "--timeout", "1", "sh -c 'sleep %s'" % marker)
        self.assertIn("0/2 passed", out)
        self.assertIn("[2 timed out]", out)
        time.sleep(0.3)
        ps = subprocess.run(["pgrep", "-f", "sleep %s" % marker], capture_output=True, text=True)
        self.assertEqual(ps.stdout.strip(), "", "orphaned child processes survived the timeout")

    def test_e10_pass_if_stdout(self):
        code, out = cli("-n", "3", "--pass-if-stdout", "PASS", "echo FAIL")
        self.assertIn("0/3 passed", out)
        code, out = cli("-n", "3", "--pass-if-stdout", "PASS", "echo PASS")
        self.assertIn("3/3 passed", out)

    def test_e11_three_commands_holm(self):
        code, out = cli("-n", "5", "true", "false", "true", "--json")
        data = json.loads(out)
        self.assertEqual(len(data["comparisons"]), 3)
        for c in data["comparisons"]:
            self.assertGreaterEqual(c["p"], c["p_raw"])  # Holm never lowers a p-value

    def test_e12_json_schema(self):
        code, out = cli("-n", "4", "true", "false", "--json")
        data = json.loads(out)
        self.assertEqual(set(data), {"version", "commands", "comparisons", "seconds"})
        c = data["commands"][0]
        self.assertEqual(set(c), {"command", "runs", "passes", "fails", "timeouts", "rate", "ci95", "verdict"})
        self.assertEqual(data["commands"][1]["verdict"], "stable fail")
        self.assertEqual(set(data["comparisons"][0]) >= {"a", "b", "diff", "p", "verdict", "significant"}, True)

    def test_e13_parallel(self):
        start = time.time()
        cli("-n", "20", "-j", "4", "sleep 1")
        self.assertLess(time.time() - start, 7.5)

    def test_until_decided_stops_early_on_obvious_gap(self):
        code, out = cli("-n", "100", "--until-decided", "--json", "true", "false")
        data = json.loads(out)
        self.assertLess(data["commands"][0]["runs"], 100)
        self.assertTrue(data["comparisons"][0]["significant"])

    def test_markdown_export(self):
        path = os.path.join(tempfile.mkdtemp(), "r.md")
        cli("-n", "3", "true", "false", "--export-markdown", path)
        md = open(path).read()
        self.assertIn("| # | Command | Passed |", md)
        self.assertIn("| Comparison |", md)

    def test_interleaved_run_indices(self):
        log = os.path.join(tempfile.mkdtemp(), "order")
        cli("-n", "3", "echo A$LUCKY_RUN >> %s" % log, "echo B$LUCKY_RUN >> %s" % log)
        self.assertEqual(open(log).read().split(), ["A0", "B0", "A1", "B1", "A2", "B2"])


if __name__ == "__main__":
    unittest.main()
