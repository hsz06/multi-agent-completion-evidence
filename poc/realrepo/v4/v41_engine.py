"""v4.1 experiment engine: strategies, metrics, Type-B analysis, early-stop,
weight sensitivity — all over the SAME 56 (case,claim) samples as v4.

Held-out per_file PASS/FAIL matrix is used ONLY for scoring detection; the
selector never reads it. Early-stop inspects only the existing-verify-set's
own real run on the mutated tree (which is exactly the verification we
measure).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))

from engine import REPOS_CFG, build_change_registry
from config import POOL_FILES, EXISTING_VERIFY_SET
from obligation.pool import build_pool
from obligation.coverage import compute_gap, greedy_select, covered_by_files, candidates_for_gap
from assertion_selector import assertion_aware_select, coverage_only_select, KIND_TO_CATEGORY
from assertion import file_sensitivity
from strategies import (assemble_cases, _file_fails, _cost, _true_break,
                        _detects, ORACLE, BASELINE, DOWNSTREAM)

RESULTS = _HERE / "results"


def _write_csv(name, rows, fields):
    with open(RESULTS / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# Strategy file selection (returns selected test files)
# ---------------------------------------------------------------------------

def select_files(case, strategy, threshold=0.8, early_stop=False):
    """Return (files_run, early_stopped_flag, skipped_extra_flag, sel_obj)."""
    comp = case["claim"].replace("_completion", "")
    existing = list(EXISTING_VERIFY_SET[case["repo"]].get(comp, []))
    g = case["gap_obj"]
    sym = case["symbol"]; kind = case["kind"]

    integ_cost = _cost(case["repo"], POOL_FILES[case["repo"]])

    if strategy in ("Local", "CoverageOnly", "AssertionAware", "IntegrationAll"):
        pass

    # Early-stop: run existing first; if any FAILs (held-out oracle = the real
    # run we're measuring), do NOT add obligations.
    existing_detects = any(_file_fails(case["case_id"], f) for f in existing)
    early_stopped = False
    if early_stop and strategy in ("AssertionAware", "CoverageOnly"):
        if existing_detects:
            early_stopped = True
            return existing, early_stopped, True, None

    if strategy == "Local":
        return existing, False, False, None
    if strategy == "IntegrationAll":
        return list(POOL_FILES[case["repo"]]), False, False, None

    if strategy == "CoverageOnly":
        sel = coverage_only_select(case["repo"], g, existing, threshold=threshold)
    elif strategy == "AssertionAware":
        sel = assertion_aware_select(case["repo"], g, existing, sym, kind,
                                     threshold=threshold)
    elif strategy == "AssertionTop1":
        # ranking-only: among candidates that bring NEW coverage, pick the
        # single highest assertion-sensitivity one (existing + 1). This isolates
        # the assertion-ranking signal from greedy set-cover's "take-all".
        cov_cands = candidates_for_gap(case["repo"], g, existing)
        # add direct-call candidates
        from obligation.pool import build_pool
        extra = []
        cov_ids = {o.obligation_id for o in cov_cands}
        for o in build_pool(case["repo"]):
            if o.target_tests in existing or o.obligation_id in cov_ids:
                continue
            s, _ = file_sensitivity(case["repo"], o.target_tests, sym, kind)
            if s > 0:
                extra.append((o, s, False))
        cands = [(o, *file_sensitivity(case["repo"], o.target_tests, sym, kind)[:1], True)
                 for o in cov_cands] + extra
        cands.sort(key=lambda x: (-x[1], -int(x[2])))
        top = cands[0][0] if cands else None
        from obligation.model import SelectionResult
        sel = SelectionResult(strategy="AssertionTop1",
                              selected=[top.obligation_id] if top else [],
                              selected_files=[top.target_tests] if top else [],
                              covered_missing=[],
                              threshold=threshold,
                              coverage_achieved=0.0,
                              estimated_cost=top.estimated_cost if top else 0.0)
    else:
        raise ValueError(strategy)
    files = list(dict.fromkeys(existing + sel.selected_files))
    return files, early_stopped, False, sel


# ---------------------------------------------------------------------------
# Main 6-strategy comparison
# ---------------------------------------------------------------------------

def run_strategies(cases, threshold=0.8):
    strategies = [("Local", None, False),
                  ("CoverageOnly@0.8", 0.8, False),
                  ("CoverageOnly@1.0", 1.0, False),
                  ("AssertionAware@0.8", 0.8, False),
                  ("AssertionAware@1.0", 1.0, False),
                  ("AssertionTop1+EarlyStop", 1.0, True),   # ranking-only top-1 pick
                  ("AssertionAware+EarlyStop", 1.0, True),
                  ("IntegrationAll", None, False)]
    n_break = sum(1 for c in cases if c["true_break"]) or 1
    per_case = []
    agg = {}
    integ_cost_by_repo = {r: _cost(r, POOL_FILES[r]) for r in POOL_FILES}
    for name, th, es in strategies:
        det = 0; ncount = 0; cost = 0.0; relcost = 0.0
        early_stop_hits = 0
        for c in cases:
            th_use = th if th is not None else (threshold if "AssertionAware" in name or "CoverageOnly" in name else 0.8)
            files, stopped, skipped, sel = select_files(c, name.split("@")[0].split("+")[0],
                                                        threshold=th_use, early_stop=es)
            detect = _detects(c, files)
            if c["true_break"] and detect:
                det += 1
            ncount += len(files)
            c_cost = _cost(c["repo"], files)
            cost += c_cost
            relcost += c_cost / max(integ_cost_by_repo[c["repo"]], 0.001)
            if stopped:
                early_stop_hits += 1
            per_case.append({"strategy": name, "case_id": c["case_id"],
                             "repo": c["repo"], "claim": c["claim"],
                             "true_break": c["true_break"], "gap": c["gap"],
                             "files_run": "|".join(files), "n_files": len(files),
                             "detect": int(detect),
                             "cost": round(c_cost, 4),
                             "relcost": round(c_cost / max(integ_cost_by_repo[c["repo"]], 0.001), 4),
                             "early_stopped": int(stopped),
                             "skipped_extra": int(skipped)})
        agg[name] = {
            "detection_rate": round(det / n_break, 4),
            "vrr": round(det / n_break, 4),
            "avg_test_count": round(ncount / len(cases), 2),
            "total_runtime": round(cost, 3),
            "avg_relative_cost": round(relcost / len(cases), 4),
            "early_stop_hits": early_stop_hits,
        }
    return agg, per_case


# ---------------------------------------------------------------------------
# Type-B专项:在 v4 的 19 个 Type-B case 上对比
# ---------------------------------------------------------------------------

def _v4_type_b_cases():
    """v4 Type-B = gap=True, existing_detects=False (Local漏检), Integration可检.
    从 v4 detection_cost.csv per_case 重建集合,然后映射到 cases。"""
    fa = json.load(open(_HERE / "results" / "failure_analysis.json"))
    # v4 failure type B examples/all
    # rebuild full list from v4 summary? we only have examples; recompute from
    # v4 per_case logic: gap & existing_detects False & true_break
    cases = assemble_cases()
    tb = []
    for c in cases:
        if not c["true_break"]:
            continue
        comp = c["claim"].replace("_completion", "")
        existing = EXISTING_VERIFY_SET[c["repo"]].get(comp, [])
        existing_detects = any(_file_fails(c["case_id"], f) for f in existing)
        integ_detects = any(_file_fails(c["case_id"], f) for f in POOL_FILES[c["repo"]])
        if c["gap"] and not existing_detects and integ_detects:
            tb.append(c)
    return cases, tb


def type_b_analysis(cases, type_b, threshold=1.0):
    rows = []
    fixed_by_assertion = 0
    still_missed = 0
    covered_missed = 0
    for c in type_b:
        # CoverageOnly selection
        comp = c["claim"].replace("_completion", "")
        existing = list(EXISTING_VERIFY_SET[c["repo"]].get(comp, []))
        co = coverage_only_select(c["repo"], c["gap_obj"], existing, threshold=threshold)
        co_files = list(dict.fromkeys(existing + co.selected_files))
        co_detect = _detects(c, co_files)
        aa = assertion_aware_select(c["repo"], c["gap_obj"], existing,
                                    c["symbol"], c["kind"], threshold=threshold)
        aa_files = list(dict.fromkeys(existing + aa.selected_files))
        aa_detect = _detects(c, aa_files)
        # failure classification
        # pool has a FAIL test (by Type-B def), so miss = selector failure
        fl = "SelectorFailure" if not aa_detect else "Detected"
        if aa_detect and not co_detect:
            fixed_by_assertion += 1
        if not aa_detect:
            still_missed += 1
        if not co_detect:
            covered_missed += 1
        # Hit@1 / Hit@3: does top-K selected extra contain a held-out FAIL file?
        def hitk(files, k):
            topk = files[len(existing):len(existing) + k]
            return int(any(_file_fails(c["case_id"], f) for f in topk))
        rows.append({
            "case_id": c["case_id"], "repo": c["repo"], "claim": c["claim"],
            "symbol": c["symbol"], "kind": c["kind"],
            "cov_only_files": "|".join(co.selected_files),
            "cov_only_detect": int(co_detect),
            "assert_aware_files": "|".join(aa.selected_files),
            "assert_aware_detect": int(aa_detect),
            "cov_hit3": hitk(co_files, 3),
            "assert_hit3": hitk(aa_files, 3),
            "failure_class": fl,
        })
    fix_rate = round(fixed_by_assertion / max(len(type_b), 1), 4)
    return {"rows": rows, "type_b_total": len(type_b),
            "fixed_by_assertion_aware": fixed_by_assertion,
            "still_missed": still_missed,
            "covered_only_missed": covered_missed,
            "type_b_fix_rate": fix_rate}


# ---------------------------------------------------------------------------
# False Expansion (Early Stop)
# ---------------------------------------------------------------------------

def false_expansion(cases, threshold=0.8):
    """For each case where existing verify-set ALREADY detects (Local would
    catch it), did the strategy still run extra obligations?"""
    out = {}
    integ_cost_by_repo = {r: _cost(r, POOL_FILES[r]) for r in POOL_FILES}
    for name in ("CoverageOnly@0.8", "AssertionAware@1.0",
                 "AssertionAware+EarlyStop"):
        existing_detects_cases = 0
        expanded = 0
        saved_tests = 0
        saved_runtime = 0.0
        for c in cases:
            comp = c["claim"].replace("_completion", "")
            existing = EXISTING_VERIFY_SET[c["repo"]].get(comp, [])
            if not any(_file_fails(c["case_id"], f) for f in existing):
                continue
            existing_detects_cases += 1
            files, stopped, skipped, sel = select_files(
                c, name.split("@")[0].split("+")[0],
                threshold=(1.0 if "1.0" in name else 0.8),
                early_stop=("EarlyStop" in name))
            extra = [f for f in files if f not in existing]
            if extra:
                expanded += 1
            # saved runtime = cost of extra that early-stop avoided
            if skipped:
                # recompute what WOULD have run without early stop
                aa = assertion_aware_select(c["repo"], c["gap_obj"], existing,
                                            c["symbol"], c["kind"], threshold=1.0)
                would_run = list(dict.fromkeys(existing + aa.selected_files))
                would_extra = [f for f in would_run if f not in existing]
                saved_runtime += _cost(c["repo"], would_extra)
                saved_tests += len(would_extra)
        out[name] = {
            "existing_detects_cases": existing_detects_cases,
            "still_expanded": expanded,
            "false_expansion_rate": round(expanded / max(existing_detects_cases, 1), 4),
            "saved_tests": saved_tests,
            "saved_runtime": round(saved_runtime, 3),
        }
    return out


# ---------------------------------------------------------------------------
# Weight sensitivity
# ---------------------------------------------------------------------------

def weight_sensitivity(cases):
    rows = []
    n_break = sum(1 for c in cases if c["true_break"]) or 1
    integ_cost_by_repo = {r: _cost(r, POOL_FILES[r]) for r in POOL_FILES}
    import assertion_selector as AS
    for alpha, beta in [(0.7, 0.3), (0.5, 0.5), (0.3, 0.7)]:
        # temporarily override default weights by monkeypatching the module fn
        orig = AS.assertion_aware_select
        def _sel(repo, gap, existing, sym, kind, threshold=1.0,
                 alpha=alpha, beta=beta):
            return orig(repo, gap, existing, sym, kind, threshold=threshold,
                        alpha=alpha, beta=beta)
        det = 0; relcost = 0.0; ncount = 0
        for c in cases:
            comp = c["claim"].replace("_completion", "")
            existing = list(EXISTING_VERIFY_SET[c["repo"]].get(comp, []))
            sel = _sel(c["repo"], c["gap_obj"], existing, c["symbol"], c["kind"], 1.0)
            files = list(dict.fromkeys(existing + sel.selected_files))
            if c["true_break"] and _detects(c, files):
                det += 1
            ncount += len(files)
            relcost += _cost(c["repo"], files) / max(integ_cost_by_repo[c["repo"]], 0.001)
        rows.append({"alpha": alpha, "beta": beta,
                     "detection_rate": round(det / n_break, 4),
                     "avg_test_count": round(ncount / len(cases), 2),
                     "avg_relative_cost": round(relcost / len(cases), 4)})
    return rows


if __name__ == "__main__":
    cases = assemble_cases()
    print(f"cases={len(cases)}")
    _, tb = _v4_type_b_cases()
    print(f"type-b cases: {len(tb)}")
    agg, pc = run_strategies(cases)
    for k, v in agg.items():
        print(f"  {k:26} det={v['detection_rate']} cost={v['avg_relative_cost']} #tests={v['avg_test_count']} es_hits={v['early_stop_hits']}")
    tba = type_b_analysis(cases, tb)
    print("type-b:", {k: tba[k] for k in ('type_b_total','fixed_by_assertion_aware','still_missed','type_b_fix_rate')})