"""Four verification strategies + core metrics, evaluated against the held-out
per-file PASS/FAIL matrix.

A "case" here is a (change_case, stale_claim) pair. A claim is stale iff it is
a downstream completion (dev_b / testing) whose existing verify-set does NOT
detect the break but the break exists in the repo (some pool file fails) —
i.e. the Triggered=true / Detected=false population from v3, PLUS Detected=true
controls.

For each strategy we determine which test files it runs, then detection =
does at least one of those files FAIL in the held-out matrix. Cost = sum of
pristine runtimes of those files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent / "v3"))   # v3
sys.path.insert(0, str(_HERE))                 # v4

from engine import build_change_registry, REPOS_CFG
from config import POOL_FILES, EXISTING_VERIFY_SET
from obligation.pool import build_pool
from obligation.coverage import compute_gap, greedy_select, covered_by_files
from obligation.model import CoverageGap, SelectionResult

ORACLE = json.load(open(_HERE / "evaluation_private_oracle" / "per_file.json"))
BASELINE = json.load(open(_HERE / "evaluation_private_oracle" / "pool_baseline.json"))
DOWNSTREAM = ("dev_b_completion", "testing_completion")


# ---------------------------------------------------------------------------
# Case assembly: (change_case, stale_claim) pairs
# ---------------------------------------------------------------------------

def _true_break(case_id: str) -> bool:
    """Break exists iff SOME pool file FAILS under this change (held-out)."""
    rec = ORACLE.get(case_id, {})
    if not rec.get("applied"):
        return False
    return any(v["result"] == "FAIL" for v in rec["per_file"].values())


def _file_fails(case_id: str, f: str) -> bool:
    rec = ORACLE.get(case_id, {})
    if not rec.get("applied"):
        return False
    return rec["per_file"].get(f, {}).get("result") == "FAIL"


def assemble_cases():
    """Build (case_id, repo, file, symbol, kind, claim, true_break, gap) rows.
    A claim is included if it is downstream AND (true_break OR has coverage gap)
    — gives both gap-positive cases and Detected=true controls."""
    cases = build_change_registry()
    out = []
    for c in cases:
        cid = c["case_id"]; repo = c["repo"]
        if not ORACLE.get(cid, {}).get("applied"):
            continue
        tb = _true_break(cid)
        for claim in DOWNSTREAM:
            comp = claim.replace("_completion", "")
            existing = EXISTING_VERIFY_SET[repo].get(comp, [])
            # existing detects?
            existing_detects = any(_file_fails(cid, f) for f in existing)
            gap = compute_gap(repo, claim, c["file"], c["symbol"])
            # include case if there's a real break on this claim's downstream
            # interest: include if true_break (break exists somewhere) — we then
            # measure per-claim detection. Also keep gap=True cases.
            if not tb:
                continue
            out.append({
                "case_id": cid, "repo": repo, "file": c["file"],
                "symbol": c["symbol"], "kind": c["kind"], "claim": claim,
                "true_break": tb,
                "existing_detects": existing_detects,
                "gap": gap.gap, "n_missing": len(gap.missing_coverage),
                "gap_obj": gap,
            })
    return out


# ---------------------------------------------------------------------------
# Strategy file selection
# ---------------------------------------------------------------------------

def strat_local(case) -> list[str]:
    """A — run only the claim's existing verify-set."""
    comp = case["claim"].replace("_completion", "")
    return list(EXISTING_VERIFY_SET[case["repo"]].get(comp, []))


def strat_dependency_only(case) -> list[str]:
    """B — v3 invalidation then rerun existing verify-set. For detection this
    is identical to Local (same files). Kept as a distinct labelled strategy."""
    return strat_local(case)


def strat_integration_all(case) -> list[str]:
    """C — run the whole verification pool."""
    return list(POOL_FILES[case["repo"]])


def strat_obligation_aware(case, threshold=0.8) -> tuple[list[str], SelectionResult]:
    """D — existing verify-set + greedy-selected obligations covering the gap."""
    comp = case["claim"].replace("_completion", "")
    existing = list(EXISTING_VERIFY_SET[case["repo"]].get(comp, []))
    sel = greedy_select(case["repo"], case["gap_obj"], existing, threshold=threshold)
    files = list(dict.fromkeys(existing + sel.selected_files))
    return files, sel


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def _cost(repo: str, files: list[str]) -> float:
    return sum(BASELINE.get(repo, {}).get(f, {}).get("duration_s", 0.5) for f in files)


def _detects(case, files: list[str]) -> bool:
    return any(_file_fails(case["case_id"], f) for f in files)


def evaluate_strategies(cases, threshold=0.8):
    """Run all 4 strategies over all cases; return per-strategy metrics +
    per-case detail rows."""
    strategies = ["Local", "DependencyOnly", "ObligationAware", "IntegrationAll"]
    per_case = []
    # integration cost reference (per repo, constant) for relative cost
    integ_cost_by_repo = {r: _cost(r, POOL_FILES[r]) for r in POOL_FILES}
    for case in cases:
        row = {"case_id": case["case_id"], "repo": case["repo"], "claim": case["claim"],
               "true_break": case["true_break"], "gap": case["gap"],
               "existing_detects": case["existing_detects"]}
        files_A = strat_local(case)
        files_B = strat_dependency_only(case)
        files_D, sel = strat_obligation_aware(case, threshold=threshold)
        files_C = strat_integration_all(case)
        for name, files in [("Local", files_A), ("DependencyOnly", files_B),
                            ("ObligationAware", files_D), ("IntegrationAll", files_C)]:
            row[f"{name}_detect"] = int(_detects(case, files))
            row[f"{name}_ncount"] = len(files)
            row[f"{name}_cost"] = round(_cost(case["repo"], files), 4)
            row[f"{name}_relcost"] = round(_cost(case["repo"], files)
                                            / max(integ_cost_by_repo[case["repo"]], 0.001), 4)
        row["OA_selected_extra"] = sel.selected_files
        row["OA_coverage_achieved"] = sel.coverage_achieved
        # failure type classification (for analysis)
        row["failure_type"] = classify_failure(case, files_D, _detects(case, files_D))
        per_case.append(row)
    # aggregate
    n = len(cases) or 1
    n_break = sum(1 for c in cases if c["true_break"]) or 1
    agg = {}
    for name in strategies:
        det = sum(r[f"{name}_detect"] for r in per_case)
        cost = sum(r[f"{name}_cost"] for r in per_case)
        relcost = sum(r[f"{name}_relcost"] for r in per_case) / n
        # VRR = correct terminal (detect when break, pass when no break).
        # All our cases are true_break=True, so correct terminal = detected.
        vrr = det / n_break  # among break cases, fraction correctly resolved to FAILED
        agg[name] = {
            "detection_rate": round(det / n_break, 4),
            "vrr": round(vrr, 4),
            "avg_test_count": round(sum(r[f"{name}_ncount"] for r in per_case) / n, 2),
            "total_runtime": round(cost, 3),
            "avg_relative_cost": round(relcost, 4),
        }
    return agg, per_case


def classify_failure(case, files_D, detects_D) -> str:
    """A-E failure typing for ObligationAware."""
    if not case["true_break"]:
        return "NO_BREAK"
    integ_detects = any(_file_fails(case["case_id"], f) for f in POOL_FILES[case["repo"]])
    if case["gap"] and detects_D:
        return "A"   # gap found, right test selected, detected
    if case["gap"] and not detects_D and not integ_detects:
        return "E"   # integration also cannot detect
    if case["gap"] and not detects_D and integ_detects:
        return "B"   # gap correct, but selected wrong test / missed
    if not case["gap"] and not detects_D and not integ_detects:
        return "E"   # no gap, integration also can't (verification insufficient globally)
    if not case["gap"] and not detects_D and integ_detects:
        return "C"   # no coverage gap (existing covers) but still can't detect -> verifier insufficiency
    return "OTHER"


if __name__ == "__main__":
    cases = assemble_cases()
    print(f"cases: {len(cases)} | gap-positive: {sum(1 for c in cases if c['gap'])}")
    agg, rows = evaluate_strategies(cases)
    for name, a in agg.items():
        print(f"{name:16} det={a['detection_rate']} vrr={a['vrr']} "
              f"ncount={a['avg_test_count']} relcost={a['avg_relative_cost']}")