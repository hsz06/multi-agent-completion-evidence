"""Held-out per-file calibration (EVALUATION ORACLE — NOT used by the selector).

Two matrices, both from REAL pytest, cached to evaluation_private_oracle/:

1. pool_baseline.json: pristine-tree runtime per (repo, pool_file) — used as
   verification COST. Built on the UNCHANGED tree.

2. per_file.json: per (case_id, pool_file) PASS/FAIL + runtime on the MUTATED
   tree. This is the held-out detection oracle: "does obligation X detect the
   break introduced by change Y". The selector NEVER reads this file.

Coverage mapping (obligation -> contracts) is built separately in pool.py from
PRISTINE-tree coverage, also before/outside any change outcome.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent / "v3"))   # v3
sys.path.insert(0, str(_HERE))                 # v4 (config)

from common.repo_driver import RepoDriver
from engine import REPOS_CFG, build_change_registry
from contracts.mutations import mutate
from config import POOL_FILES

ORACLE_DIR = _HERE / "evaluation_private_oracle"
ORACLE_DIR.mkdir(parents=True, exist_ok=True)


def calibrate_pool_baseline():
    """Pristine-tree: runtime + result per pool file (cost reference)."""
    out = {}
    for repo, files in POOL_FILES.items():
        d = RepoDriver(repo)
        ignore = REPOS_CFG[repo]["test_ignore"]
        out[repo] = {}
        for f in files:
            r = d.run_pytest([f], extra=["--ignore=" + ig for ig in ignore] if ignore else [])
            out[repo][f] = {"result": r["result"], "duration_s": r["duration_s"],
                            "tests_failed": r["tests_failed"]}
            print(f"[baseline] {repo}/{f}: {r['result']} {r['duration_s']}s", flush=True)
        d.cleanup()
    json.dump(out, open(ORACLE_DIR / "pool_baseline.json", "w"), indent=2)
    return out


def calibrate_per_file():
    """Per (case, pool_file) PASS/FAIL on the mutated tree. Held-out oracle."""
    cases = build_change_registry()
    out = {}
    for case in cases:
        repo = case["repo"]
        d = RepoDriver(repo)
        orig = d.read(case["file"])
        try:
            new_src = mutate(orig, case["symbol"], case["kind"])
        except Exception as e:
            d.cleanup()
            out[case["case_id"]] = {"applied": False, "error": str(e)[:120]}
            continue
        if new_src == orig:
            d.cleanup()
            out[case["case_id"]] = {"applied": False, "error": "no-op"}
            continue
        d.write(case["file"], new_src)
        ignore = REPOS_CFG[repo]["test_ignore"]
        per_file = {}
        for f in POOL_FILES[repo]:
            r = d.run_pytest([f], extra=["--ignore=" + ig for ig in ignore] if ignore else [])
            per_file[f] = {"result": r["result"], "duration_s": r["duration_s"],
                           "tests_failed": r["tests_failed"]}
        d.cleanup()
        out[case["case_id"]] = {"applied": True, "repo": repo,
                                 "symbol": case["symbol"], "kind": case["kind"],
                                 "file": case["file"], "per_file": per_file}
        print(f"[per_file] {case['case_id']}: "
              f"{sum(1 for v in per_file.values() if v['result']=='FAIL')} failing files",
              flush=True)
    json.dump(out, open(ORACLE_DIR / "per_file.json", "w"), indent=2)
    return out


if __name__ == "__main__":
    t0 = time.time()
    print("== pool baseline (pristine) ==", flush=True)
    calibrate_pool_baseline()
    print("== per-file held-out oracle (mutated) ==", flush=True)
    calibrate_per_file()
    print(f"done in {time.time()-t0:.1f}s", flush=True)