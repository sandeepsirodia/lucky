<h1 align="center">lucky</h1>

<p align="center">
  <em>hyperfine, but for pass rates. Is it better, or did you get lucky?</em>
</p>

<p align="center">
  <a href="https://github.com/sandeepsirodia/lucky/actions/workflows/ci.yml"><img src="https://github.com/sandeepsirodia/lucky/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/dependencies-0-111111?style=flat-square" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/stats-from%20scratch%2C%20tested%20against%20references-111111?style=flat-square" alt="Stats from scratch">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT">
</p>

---

You tweak a prompt. You run your eval. **6/6.** Before the tweak it was **2/6.** Ship it!

That really happened to me. Same prompt, same model, same eval, run twice with no changes: 2/6, then 6/6. Nothing got better. I got lucky.

```
# lucky's verdict on those two runs (2/6 vs 6/6):
#1 vs #2: No detectable difference (p=0.061). To detect a gap this size, run ~n=10 each.
```

Pass rates lie at small n, and we all read them anyway: prompt tweaks, model swaps, agent configs, "is this flaky test fixed now?". **lucky runs the thing enough times and tells you what you actually know.**

## Use it

```bash
uvx --from git+https://github.com/sandeepsirodia/lucky lucky -n 20 './eval.sh --model a' './eval.sh --model b'
```

```console
#1: ./eval.sh --model a
  17/20 passed (85%, 95% CI 64–95%)   flaky (17/20 passed)

#2: ./eval.sh --model b
  12/20 passed (60%, 95% CI 39–78%)   flaky (12/20 passed)

#1 vs #2: No detectable difference (p=0.16). To detect a gap this size, run ~n=57 each.
```

85% vs 60% looks like a blowout. It isn't one yet. lucky tells you how many more runs it would take to know.

Any command that exits `0` on success works: tests, evals, scripts, `curl` health checks, agent runs.

## Is my test flaky, or did I fix it?

```bash
lucky -n 50 -j 8 'pytest tests/test_checkout.py -x -q'
```

```
  50/50 passed (100%, 95% CI 93–100%)   stable pass
```

"Passed 3 times in a row" means very little. **50/50 with a 95% interval of 93–100%** is a claim you can put in a PR description.

## Everything it does

| | |
|---|---|
| `lucky -n 20 'cmd'` | Pass rate + 95% Wilson interval + `stable pass` / `stable fail` / `flaky` |
| `lucky 'a' 'b' 'c'` | Every pair compared with Fisher's exact test, Holm-corrected for 3+ commands |
| `-j 8` | Parallel runs. Each gets `$LUCKY_RUN` (0, 1, 2…) |
| `--until-decided` | Stop early once the answer is clear, **without cheating** (see below) |
| `--timeout 60` | Hung runs count as fails, and their whole process tree is killed |
| `--pass-if-stdout 'PASS'` | For commands that exit 0 but print the verdict |
| `--json` · `--export-markdown r.md` | For CI and PR descriptions |

Runs are **interleaved** (A, B, A, B…), so if your API gets slower at 3pm, both commands feel it equally.

## Peeking is cheating, and here's the number

The tempting move: run a few, check, run a few more, check again, stop when it looks significant. That inflates false wins. I simulated two *identical* coins, checking for a "significant" difference:

| Strategy | False "B is better" rate |
|---|---|
| Check once at the end | 3.8% (Fisher's test is slightly conservative, so it stays under the promised 5%) |
| Check at 5 points, stop when p < 0.05 | **9.4%** |
| Check after every single run | **16%** |

`--until-decided` checks at 5 pre-declared points and splits the 5% error budget across them. Simulated false-positive rate: **1.75%**, and it still catches real gaps (0.9 vs 0.4) over 90% of the time. The simulation is [a test](tests/test_lucky.py) that runs in CI.

## No magic, no dependencies

Every statistic is implemented in [`lucky.py`](lucky.py) with the standard library and checked in tests against independent references:
- the **Wilson interval** against published values
- **Fisher's exact test** against brute-force enumeration over 200 random tables
- **Holm** against a worked example
- the **sample-size estimate** (Fleiss continuity correction) against the formula, and by simulation: at the suggested n, a real gap is actually detected about 80% of the time

It's one file. Read it in a sitting.

## Honest limits

- It answers "is there a detectable difference?", not "how big is the real difference?". Look at the two intervals for that.
- It assumes runs are independent. If your command caches results between runs, lucky can't know.
- `--until-decided` uses a simple, conservative stopping rule (Bonferroni across looks). It's safe, but it stops later than fancier designs would.

## Prior art, and what's new here

- **[hyperfine](https://github.com/sharkdp/hyperfine)** is the inspiration for the interface. It measures time; lucky measures pass rates.
- **Flaky-test rerunners** ([pytest-flakefinder](https://github.com/dropbox/pytest-flakefinder), [flaky](https://github.com/box/flaky), `go test -count`) rerun tests inside one framework. Use them if you live in that framework.
- **Eval statistics libraries** ([evalstats](https://github.com/clavis-systems/evalstats), [evalci](https://arxiv.org/abs/2607.04429)) are Python libraries you call on results tables, and they're more complete statistically.

lucky's niche is narrow on purpose: **any command, from the shell, with a verdict**, plus early stopping that doesn't cheat.

<details>
<summary><b>Development</b></summary>

```bash
python -m unittest discover -s tests -v
```

Tests map 1:1 to [SPEC.md](SPEC.md). Installed as a package it's called `passrate` (the name `lucky` was taken on PyPI); the command is still `lucky`.

</details>

<p align="center"><sub>MIT © Sandeep Sirodia · If lucky just saved you from shipping a coin flip, a ⭐ helps the next person find it.</sub></p>
