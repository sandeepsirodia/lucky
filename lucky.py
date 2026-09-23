"""lucky: hyperfine, but for pass rates. Is it better, or did you get lucky?

Runs pass/fail commands many times and tells you, with real statistics, whether a command
is flaky and whether one command really beats another. Standard library only; every
statistic is implemented here and checked against reference values in the tests.
"""
import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import NormalDist

__version__ = "0.1.0"
ALPHA = 0.05


# ------------------------------------------------------------------ statistics

def wilson(k, n, conf=0.95):
    """Wilson score interval for k successes out of n."""
    if n == 0:
        return 0.0, 1.0
    z = NormalDist().inv_cdf(1 - (1 - conf) / 2)
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo, hi = (centre - half) / denom, (centre + half) / denom
    return (0.0 if k == 0 else max(0.0, lo)), (1.0 if k == n else min(1.0, hi))


def _log_comb(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact(a, b, c, d):
    """Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]].
    Sums the probabilities of all tables (same margins) no more likely than the observed one."""
    n, row1, col1 = a + b + c + d, a + b, a + c
    lo, hi = max(0, col1 - (n - row1)), min(row1, col1)
    logp = lambda x: _log_comb(row1, x) + _log_comb(n - row1, col1 - x) - _log_comb(n, col1)  # noqa: E731
    observed = logp(a)
    total = sum(math.exp(logp(x)) for x in range(lo, hi + 1) if logp(x) <= observed + 1e-7)
    return min(1.0, total)


def holm(pvalues):
    """Holm-Bonferroni adjusted p-values, same order as the input."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def sample_size(p1, p2, alpha=ALPHA, power=0.8):
    """Runs per command to detect p1 vs p2 (two-sided) with Fleiss' continuity correction,
    which matches exact tests like Fisher's far better than the plain normal formula at small n."""
    if p1 == p2:
        return None
    nd = NormalDist()
    za, zb = nd.inv_cdf(1 - alpha / 2), nd.inv_cdf(power)
    pbar, delta = (p1 + p2) / 2, abs(p1 - p2)
    n = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / delta ** 2
    return math.ceil(n / 4 * (1 + math.sqrt(1 + 4 / (n * delta))) ** 2)


def looks_schedule(max_n, looks):
    """Pre-declared interim analysis points (per command)."""
    return sorted({max(1, round(max_n * (i + 1) / looks)) for i in range(looks)})


def sequential_compare(draw_a, draw_b, max_n, looks=5, alpha=ALPHA):
    """Group-sequential comparison with a Bonferroni split of alpha across the pre-declared looks.
    Stops as soon as a look is significant at alpha/looks, which keeps the overall false-positive
    rate <= alpha. Returns (passes_a, passes_b, n_used, significant)."""
    # ponytail: Bonferroni over looks is conservative; O'Brien-Fleming boundaries would stop sooner
    sched = looks_schedule(max_n, looks)
    per_look = alpha / len(sched)
    a = b = n = 0
    for target in sched:
        while n < target:
            a += draw_a()
            b += draw_b()
            n += 1
        if fisher_exact(a, n - a, b, n - b) < per_look:
            return a, b, n, True
    return a, b, n, False


# ------------------------------------------------------------------ running

def run_once(cmd, index, timeout, pass_re):
    env = dict(os.environ, LUCKY_RUN=str(index))
    p = subprocess.Popen(cmd, shell=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        return "timeout"
    if p.returncode != 0:
        return "fail"
    if pass_re is not None and not pass_re.search(out):
        return "fail"
    return "pass"


class Tally:
    def __init__(self, cmd):
        self.cmd, self.passes, self.fails, self.timeouts = cmd, 0, 0, 0

    @property
    def n(self):
        return self.passes + self.fails + self.timeouts

    def add(self, outcome):
        if outcome == "pass":
            self.passes += 1
        elif outcome == "timeout":
            self.timeouts += 1
        else:
            self.fails += 1


def run_batch(tallies, runs, jobs, timeout, pass_re, start):
    """Run `runs` more attempts per command, interleaved (A, B, A, B…) so drift hits both equally."""
    work = [(t, start + i) for i in range(runs) for t in tallies]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for (t, _), outcome in zip(work, pool.map(lambda w: run_once(w[0].cmd, w[1], timeout, pass_re), work)):
            t.add(outcome)


def verdict_single(t):
    if t.passes == t.n:
        return "stable pass"
    if t.passes == 0:
        return "stable fail"
    return "flaky (%d/%d passed)" % (t.passes, t.n)


def compare(tallies, alpha=ALPHA, per_look_alpha=None):
    pairs = [(i, j) for i in range(len(tallies)) for j in range(i + 1, len(tallies))]
    raw = [fisher_exact(tallies[i].passes, tallies[i].n - tallies[i].passes,
                        tallies[j].passes, tallies[j].n - tallies[j].passes) for i, j in pairs]
    adj = holm(raw) if len(pairs) > 1 else raw
    threshold = per_look_alpha or alpha
    out = []
    for (i, j), p_raw, p in zip(pairs, raw, adj):
        a, b = tallies[i], tallies[j]
        ra, rb = a.passes / a.n, b.passes / b.n
        entry = {"a": i, "b": j, "diff": rb - ra, "p": p, "p_raw": p_raw, "significant": p < threshold}
        if entry["significant"]:
            better = b if rb > ra else a
            entry["verdict"] = "%s is better (p=%s)" % (label(better, tallies), fmt_p(p))
        else:
            n_need = sample_size(ra, rb)
            if n_need is not None:
                n_need = max(n_need, min(a.n, b.n) + 1)  # never suggest fewer runs than already failed to decide
            more = "" if n_need is None else " To detect a gap this size, run ~n=%d each." % n_need
            entry["verdict"] = "No detectable difference (p=%s).%s" % (fmt_p(p), more)
            entry["n_needed"] = n_need
        out.append(entry)
    return out


def label(t, tallies):
    return "#%d" % (tallies.index(t) + 1)


def fmt_p(p):
    return "%.2g" % p if p >= 0.001 else "<0.001"


# ------------------------------------------------------------------ output

def report_text(tallies, pairs, out):
    for i, t in enumerate(tallies, 1):
        lo, hi = wilson(t.passes, t.n)
        out.write("#%d: %s\n" % (i, t.cmd))
        out.write("  %d/%d passed (%.0f%%, 95%% CI %.0f–%.0f%%)%s   %s\n\n" % (
            t.passes, t.n, 100 * t.passes / t.n, 100 * lo, 100 * hi,
            "  [%d timed out]" % t.timeouts if t.timeouts else "", verdict_single(t)))
    for pr in pairs:
        out.write("#%d vs #%d: %s\n" % (pr["a"] + 1, pr["b"] + 1, pr["verdict"]))


def as_json(tallies, pairs):
    cmds = []
    for t in tallies:
        lo, hi = wilson(t.passes, t.n)
        cmds.append({"command": t.cmd, "runs": t.n, "passes": t.passes, "fails": t.fails, "timeouts": t.timeouts,
                     "rate": t.passes / t.n, "ci95": [lo, hi], "verdict": verdict_single(t)})
    return {"version": __version__, "commands": cmds, "comparisons": pairs}


def as_markdown(tallies, pairs):
    rows = ["| # | Command | Passed | Rate | 95% CI | Verdict |", "|---|---|---|---|---|---|"]
    for i, t in enumerate(tallies, 1):
        lo, hi = wilson(t.passes, t.n)
        rows.append("| %d | `%s` | %d/%d | %.0f%% | %.0f–%.0f%% | %s |" % (
            i, t.cmd.replace("|", "\\|"), t.passes, t.n, 100 * t.passes / t.n, 100 * lo, 100 * hi, verdict_single(t)))
    if pairs:
        rows += ["", "| Comparison | Δ | p (Holm) | Verdict |", "|---|---|---|---|"]
        rows += ["| #%d vs #%d | %+.0f pts | %s | %s |" % (p["a"] + 1, p["b"] + 1, 100 * p["diff"], fmt_p(p["p"]),
                                                            p["verdict"]) for p in pairs]
    return "\n".join(rows) + "\n"


# ------------------------------------------------------------------ CLI

def main(argv=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="lucky", description="hyperfine, but for pass rates.")
    ap.add_argument("commands", nargs="+", help="shell command(s); exit 0 = pass. Two or more are compared.")
    ap.add_argument("-n", "--runs", type=int, default=20, help="runs per command (default 20; max with --until-decided)")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="parallel runs (default 1)")
    ap.add_argument("--timeout", type=float, help="seconds per run; a timeout counts as a fail")
    ap.add_argument("--pass-if-stdout", metavar="REGEX", help="also require stdout to match REGEX")
    ap.add_argument("--until-decided", action="store_true",
                    help="stop early once 2 commands differ significantly (pre-declared looks, alpha split across them)")
    ap.add_argument("--looks", type=int, default=5, help="interim looks for --until-decided (default 5)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--export-markdown", metavar="FILE", help="write a Markdown results table")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    if a.runs < 1:
        ap.error("-n must be >= 1")
    pass_re = re.compile(a.pass_if_stdout) if a.pass_if_stdout else None
    tallies = [Tally(c) for c in a.commands]
    start = time.time()

    per_look = None
    if a.until_decided and len(tallies) == 2:
        sched = looks_schedule(a.runs, a.looks)
        per_look = ALPHA / len(sched)
        done = 0
        for target in sched:
            run_batch(tallies, target - done, a.jobs, a.timeout, pass_re, done)
            done = target
            x, y = tallies
            if fisher_exact(x.passes, x.n - x.passes, y.passes, y.n - y.passes) < per_look:
                break
    else:
        run_batch(tallies, a.runs, a.jobs, a.timeout, pass_re, 0)

    pairs = compare(tallies, per_look_alpha=per_look) if len(tallies) > 1 else []
    if a.json:
        data = as_json(tallies, pairs)
        data["seconds"] = round(time.time() - start, 2)
        out.write(json.dumps(data, indent=2) + "\n")
    else:
        report_text(tallies, pairs, out)
    if a.export_markdown:
        with open(a.export_markdown, "w", encoding="utf-8") as f:
            f.write(as_markdown(tallies, pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
