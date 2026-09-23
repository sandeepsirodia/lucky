# lucky — SPEC

> hyperfine, but for pass rates. *Is it better, or did you get lucky?*

Runs any pass/fail command many times and tells you, with real statistics, whether it's flaky, and whether command B really beats command A or you're looking at noise.

## Who it's for
- Anyone comparing prompts, models, skills or agent configs by "it seemed better on the last run".
- Anyone chasing a flaky test ("it passed 3 times, so it's fixed?").
- Eval builders who want a verdict, not just two percentages.

## Must have (v1)
1. **`lucky -n N '<cmd>'`** (quoted commands, like hyperfine): runs the command N times; exit code 0 = pass. Prints passes/N, the pass rate, and a **95% Wilson interval**.
2. **Compare mode:** `lucky -n N '<cmdA>' '<cmdB>'` (2+ commands, runs interleaved A, B, A, B so drift hits both equally). For each pair: the difference in rate, a **Fisher's exact test** p-value, and a one-line verdict:
   - `B is better (p=0.003)`
   - `No detectable difference (p=0.41). To detect a gap this size, run ~n=120 each.`
3. **Sample-size estimate:** when there's no detectable difference, estimate the N needed to detect the observed gap at 80% power (two-proportion formula with Fleiss continuity correction, never less than the runs already done).
4. **Flaky verdict** for a single command: `stable pass`, `stable fail`, or `flaky (13/20 passed)`.
5. **Early stop, opt-in:** `--until-decided` stops once the verdict is settled, using a pre-declared sequential rule. There's no peeking-then-deciding: the correction is built into the stopping rule, and a test proves it keeps the false-positive rate controlled.
6. **Parallel runs:** `-j 4`. Each run gets `LUCKY_RUN=<i>` in its environment.
7. **Timeouts:** `--timeout 60`. A timeout counts as a fail and is reported separately.
8. **Machine output:** `--json`, and `--export-markdown` to write a results table.
9. **Custom pass criterion:** `--pass-if-stdout <regex>` for commands that exit 0 but print PASS/FAIL.
10. **Stdlib only.** Wilson, Fisher (exact, via log-factorials), and power are implemented from scratch.

## Won't do (v1)
- Timing benchmarks (use hyperfine).
- Bayesian analysis, or more than two outcome categories.
- Multiple-comparison correction beyond Holm for 3+ commands. Holm is in scope; anything fancier is not.

## Expectations → test cases

| ID | Given | When | Then |
|---|---|---|---|
| E1 | 17 passes out of 20 | Wilson 95% | Matches the reference value (0.640, 0.948) to 3 decimal places |
| E2 | Edge cases 0/20 and 20/20 | Wilson | Bounds stay within [0, 1]; 0/20 lower bound is exactly 0 |
| E3 | A = 17/20, B = 12/20 | Fisher two-sided | p matches the reference (scipy.stats.fisher_exact) to 4 decimal places; the verdict says no detectable difference |
| E4 | A = 45/50, B = 20/50 | Compare | Verdict: A is better, p < 0.001 |
| E5 | A command that always exits 0 | `lucky -n 10` | 10/10, verdict `stable pass` |
| E6 | A script that fails on alternate runs (via `LUCKY_RUN`) | `lucky -n 20` | 10/20, verdict `flaky` |
| E7 | A command that sleeps past `--timeout 1` | Run | Counted as a fail, reported as a timeout; its child processes are killed (no orphans) |
| E8 | Simulation: 2,000 A/A comparisons (identical coins, p=0.7) with `--until-decided` | Measure the false-positive rate | ≤ 5% (± simulation error). Proves early stopping doesn't cheat |
| E9 | No detectable difference, observed 0.85 vs 0.60 | Sample-size estimate | Within 10% of the two-proportion power formula with Fleiss' continuity correction, and a simulation at that n detects the gap ≥ ~80% of the time |
| E10 | `--pass-if-stdout 'PASS'` with a command that exits 0 but prints FAIL | Run | Counted as a fail |
| E11 | 3 commands | Compare | All 3 pairs reported with Holm-adjusted p-values |
| E12 | `--json` | Run | Valid JSON with per-command counts, intervals and pairwise results; schema test |
| E13 | `-j 4` with 20 runs of `sleep 1` | Wall time | ≤ ~6 s (runs really are parallel) |

## Launch number
Rerun a real flip-flop: absolutely-not's pushback case scored 2/6, then 6/6, on consecutive runs. `lucky` shows whether that difference means anything and how many runs you'd need to know. Commit the raw output.

## Done when
- E1–E13 pass. The README leads with a real before/after: "I thought B was better. lucky said p=0.41."
- Naming note: PyPI `lucky` is taken, so publish the package as **`passrate`** (free on PyPI and npm), with the command still called `lucky`.
