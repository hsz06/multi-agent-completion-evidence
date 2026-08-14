# Contract-Obligation PoC — Multi-Agent Stale Completion Evidence & Verification Obligation Selection

A four-stage research PoC investigating a single question:

> When a long-horizon multi-agent software task modifies a shared contract,
> other agents' earlier "completed" claims can silently go stale. Can we (a)
> precisely invalidate stale completion evidence, and (b) — without generating
> new tests — select minimal **existing** verification obligations that
> actually detect the break, at a fraction of full integration-test cost?

Each stage attacks a specific external-validity risk exposed by the previous
one. Reports are honest about negative results: the final verdict across all
stages is **UNCERTAIN**, and the dominant ceiling is shown to be *verifier
pool capability / semantic detectability*, not the selector algorithm.

## Stages

### v1 — `poc/` : completion-evidence invalidation (toy PoC)
Reproduces Global False Completion (Local-VERIFIED but Global-FAILED),
shows evidence invalidation blocks it, and recovers a missing dependency via
counterfactual replay + regression gate. 19 unit tests.
Report: `poc/EXPERIMENT_REPORT.md` → verdict **YES** (mechanism established).

### v2 — `poc/realrepo/` : real open-source repos
3 real repos (tinydb, cerberus, boltons); real-pytest oracle; missing-dependency
recovery with counterfactual replay. Report: `REALREPO_EXPERIMENT_REPORT.md` →
**UNCERTAIN** (problem real but narrow; strong testing erases most GFC).

### v3 — `poc/realrepo/v3/` : contract-level + real-LLM pilot
5 repos (+ toolz, pyparsing); contract-level dependency graph (288 instances,
75 critical edges); deterministic simulated-agent track + 3 real LLM agent
trajectories (strictly separated, not conflated). Report: `V3_RESEARCH_REPORT.md`
→ **UNCERTAIN** (real-LLM external validity not established; 4-stage).

### v4 / v4.1 / v4.2 — `poc/realrepo/v4/` : verification obligation selection
The core contribution. From an **existing** test pool (no new tests), select
minimal obligations whose real pytest detects the contract-change break.
- **v4** `VERIFICATION_OBLIGATION_REPORT.md`: line-level coverage gap +
  greedy set-cover → **0.66–0.82 detection @ 23–36% of integration cost**.
- **v4.1** `V4_1_ASSERTION_AWARE_REPORT.md`: assertion-sensitivity ranking +
  EarlyStop. **Detection 0.821 @ 27.6% cost**; EarlyStop drops False-Expansion
  1.0→0.0. Assertion ranking signal is valid but greedy set-cover dissolves it.
- **v4.2** `V4_2_HYBRID_SEMANTIC_REPORT.md`: private-contract extraction
  (**zero contribution** — corrects a v4.1 misattribution) + real LLM semantic
  candidate augmentation (GLM-5.2, 241 calls). **SemanticRescueRate = 0**;
  remaining 16% failures are history-compatibility-type that no permissible
  signal (no FAIL labels) can nominate. Ceiling = verifier pool detectability.

## Key honest findings
- Global False Completion is real and reproducible (v1–v3), but largely erased
  by strong integration testing.
- Contract-level obligation selection reaches **~0.82–0.84 detection at ~28%
  of integration cost** with existing tests only — a real, cheap, Pareto
  improvement (v4→v4.1).
- The **remaining ~16% is a hard ceiling**: failures where the only detecting
  test depends on historical API compatibility that static AST + coverage +
  assertion + LLM-semantic (without seeing FAIL labels) cannot nominate.
- Private-contract extraction and LLM semantic augmentation gave **zero
  detection increment** on this dataset (v4.2). Reports document this rather
  than hide it.

## Reproduce
Python 3.9; only `pytest` + `coverage` + `requests` needed.

```bash
# v1
cd poc && python3 run_experiments.py && python3 -m pytest tests/ -q

# v2 (needs repos cloned + calibration — see poc/realrepo/README)
cd poc/realrepo && python3 oracle_calibration.py && python3 run_realrepo_experiments.py

# v3 / v4 (reuse v2 calibration)
cd poc/realrepo/v3 && python3 run_v3_experiments.py --mode deterministic
cd poc/realrepo/v4 && python3 run_verification_obligation_poc.py
cd poc/realrepo/v4 && python3 run_assertion_aware_experiments.py
cd poc/realrepo/v4 && python3 -m v4.v42_experiments        # LLM track (needs ANTHROPIC_* env or falls back)
```

Repos are vendored under `poc/realrepo/repos/` (shallow clones; commits in
`repo_manifest.json`). The held-out per-file PASS/FAIL oracle is committed
under `poc/realrepo/v4/evaluation_private_oracle/` for reproducibility of the
reported numbers; regenerate with `python3 -m v4.calibrate_per_file`.

## Layout
```
poc/                         v1 (toy)
poc/realrepo/                v2 (repos, calibration, G*, recovery)
poc/realrepo/v3/             v3 (contract-level, simulated + real-LLM pilot)
poc/realrepo/v4/             v4 / v4.1 / v4.2 (obligation selection, assertion, LLM)
  experiments_*.py, run_*    entry points
  evaluation_private_oracle/ held-out per-file matrix (committed)
  results/                   all CSV/JSON
  *_REPORT.md                per-stage reports (read these)
```

## License
MIT — see `LICENSE`.