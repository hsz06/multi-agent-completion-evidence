"""v4.1 unified entry: python3 v4/run_assertion_aware_experiments.py

Produces v4.1 result files (does NOT overwrite v4 originals):
  assertion_sensitivity.csv, assertion_ranking.csv, v41_detection_cost.csv,
  type_b_analysis.csv, early_stop.csv, weight_sensitivity.csv, v41_summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))

from engine import REPOS_CFG, build_change_registry
from config import POOL_FILES, EXISTING_VERIFY_SET
from obligation.pool import build_pool
from obligation.coverage import compute_gap, covered_by_files
from assertion import file_sensitivity
from assertion_selector import assertion_aware_select, coverage_only_select
from strategies import assemble_cases, _file_fails, _cost, _detects, ORACLE
from v41_engine import (run_strategies, type_b_analysis, _v4_type_b_cases,
                        false_expansion, weight_sensitivity, _write_csv, RESULTS)

ARGS = None


def emit_assertion_sensitivity(cases):
    rows = []
    seen = set()
    for c in cases:
        repo = c["repo"]; sym = c["symbol"]; kind = c["kind"]
        for o in build_pool(repo):
            f = o.target_tests
            k = (repo, f, sym)
            if k in seen:
                continue
            seen.add(k)
            sens, ev = file_sensitivity(repo, f, sym, kind)
            rows.append({
                "repo": repo, "test_file": f,
                "contract": f"{c['file']}::FUNCTION_SIGNATURE::{sym}",
                "change_kind": kind,
                "coverage_score": int(any(sym in s.split('.')[-1] and sym.split('.')[-1] == s.split('.')[-1]
                                          for s in o.covered_symbols)),
                "assertion_sensitivity": sens,
                "evidence": json.dumps(ev, separators=(",", ":"))[:200],
            })
    _write_csv("assertion_sensitivity.csv", rows,
               ["repo", "test_file", "contract", "change_kind",
                "coverage_score", "assertion_sensitivity", "evidence"])
    return rows


def emit_assertion_ranking(cases):
    """For each (case,claim), rank pool candidates by assertion sensitivity
    and report Hit@1/@3 against held-out FAIL files."""
    rows = []
    for c in cases:
        repo = c["repo"]; sym = c["symbol"]; kind = c["kind"]
        comp = c["claim"].replace("_completion", "")
        existing = set(EXISTING_VERIFY_SET[repo].get(comp, []))
        # candidate pool: cover the gap OR direct-call
        g = c["gap_obj"]
        req = set(g.required_contracts)
        cur = set(covered_by_files(repo, list(existing)))
        missing = req - cur
        cands = []
        for o in build_pool(repo):
            if o.target_tests in existing:
                continue
            covers = bool(set(o.covered_contracts) & missing)
            sens, ev = file_sensitivity(repo, o.target_tests, sym, kind)
            calls = sens > 0
            if covers or calls:
                cands.append((o, sens, covers))
        cands.sort(key=lambda x: (-x[1], -int(x[2])))
        # Hit@K vs held-out FAIL
        fails = {f for f in POOL_FILES[repo] if _file_fails(c["case_id"], f)
                 and f not in existing}
        def hitk(k):
            topk = [o.target_tests for o, _, _ in cands[:k]]
            return int(any(f in topk for f in fails))
        rows.append({
            "case_id": c["case_id"], "repo": repo, "claim": c["claim"],
            "symbol": sym, "kind": kind,
            "n_candidates": len(cands),
            "rank1": (cands[0][0].target_tests if cands else ""),
            "rank1_sens": (cands[0][1] if cands else ""),
            "hit1": hitk(1), "hit3": hitk(3),
            "n_heldout_fail_in_candidates": len(fails & {o.target_tests for o, _, _ in cands}),
        })
    _write_csv("assertion_ranking.csv", rows,
               ["case_id", "repo", "claim", "symbol", "kind", "n_candidates",
                "rank1", "rank1_sens", "hit1", "hit3",
                "n_heldout_fail_in_candidates"])
    return rows


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--selector", choices=["coverage", "assertion"], default="assertion")
    ap.add_argument("--early-stop", choices=["on", "off"], default="on")
    ap.add_argument("--threshold", type=float, default=1.0)
    ARGS = ap.parse_args()
    t0 = time.time()
    print(f"[v4.1] selector={ARGS.selector} early-stop={ARGS.early_stop} threshold={ARGS.threshold}", flush=True)

    cases = assemble_cases()
    print(f"[v4.1] cases={len(cases)}", flush=True)

    # 1. assertion sensitivity per (repo,test,contract)
    emit_assertion_sensitivity(cases)
    # 2. assertion ranking hit@k
    emit_assertion_ranking(cases)
    # 3. main 6-strategy detection/cost
    agg, per_case = run_strategies(cases)
    _write_csv("v41_detection_cost.csv",
               [{"strategy": k, **v} for k, v in agg.items()],
               ["strategy", "detection_rate", "vrr", "avg_test_count",
                "total_runtime", "avg_relative_cost", "early_stop_hits"])
    # 4. type-b
    _, tb = _v4_type_b_cases()
    tbres = type_b_analysis(cases, tb)
    _write_csv("type_b_analysis.csv", tbres["rows"],
               ["case_id", "repo", "symbol", "kind", "cov_only_files",
                "cov_only_detect", "assert_aware_files", "assert_aware_detect",
                "cov_hit3", "assert_hit3", "failure_class"])
    # 5. early stop / false expansion
    fe = false_expansion(cases)
    _write_csv("early_stop.csv",
               [{"strategy": k, **v} for k, v in fe.items()],
               ["strategy", "existing_detects_cases", "still_expanded",
                "false_expansion_rate", "saved_tests", "saved_runtime"])
    # 6. weight sensitivity
    ws = weight_sensitivity(cases)
    _write_csv("weight_sensitivity.csv", ws,
               ["alpha", "beta", "detection_rate", "avg_test_count", "avg_relative_cost"])

    summary = {
        "config": {"selector": ARGS.selector, "early_stop": ARGS.early_stop,
                   "threshold": ARGS.threshold},
        "n_cases": len(cases),
        "aggregate": agg,
        "type_b": {k: tbres[k] for k in ("type_b_total", "fixed_by_assertion_aware",
                                         "still_missed", "type_b_fix_rate")},
        "false_expansion": fe,
        "weight_sensitivity": ws,
    }
    json.dump(summary, open(RESULTS / "v41_summary.json", "w"), indent=2)

    # print headline
    print(f"\n{'Strategy':<28}{'Detect':>8}{'VRR':>7}{'#Tst':>6}{'RelCost':>9}{'ES':>4}")
    for k, v in agg.items():
        print(f"{k:<28}{v['detection_rate']:>8.3f}{v['vrr']:>7.3f}"
              f"{v['avg_test_count']:>6.2f}{v['avg_relative_cost']:>9.3f}{v['early_stop_hits']:>4}")
    print(f"\nType-B: total={tbres['type_b_total']} fixed={tbres['fixed_by_assertion_aware']} "
          f"missed={tbres['still_missed']} fix_rate={tbres['type_b_fix_rate']}")
    print(f"FalseExpansion: {json.dumps(fe, separators=(',', ':'))}")
    print(f"\n[done] v4.1 in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()