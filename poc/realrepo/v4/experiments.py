"""v4 experiments driver: produce all result files.

  obligation_selection.csv  — per-case gap + selected obligations
  coverage_gap.csv          — per-(case,claim) required/covered/missing
  detection_cost.csv        — main 4-strategy table (per-case + aggregate)
  threshold_sensitivity.csv — ObligationAware @ threshold 0.6/0.8/1.0
  coverage_sensitivity.csv  — existing-verify-set regime LOCAL/MODULE/INTEGRATION
  failure_analysis.json     — A-E typing with examples
  summary.json              — headline numbers
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))

from engine import REPOS_CFG, build_change_registry
from config import POOL_FILES, EXISTING_VERIFY_SET
from obligation.pool import build_pool
from obligation.coverage import compute_gap, greedy_select, covered_by_files
from strategies import (assemble_cases, evaluate_strategies, strat_local,
                        strat_dependency_only, strat_obligation_aware,
                        strat_integration_all, _detects, _cost, _file_fails,
                        _true_break, DOWNSTREAM, ORACLE, BASELINE)

RESULTS = _HERE / "results"
RESULTS.mkdir(exist_ok=True)


def _write_csv(name, rows, fields):
    with open(RESULTS / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# 1. coverage_gap.csv
# ---------------------------------------------------------------------------

def exp_coverage_gap(cases):
    rows = []
    for c in cases:
        g = c["gap_obj"]
        rows.append({"case_id": c["case_id"], "repo": c["repo"], "claim": c["claim"],
                     "changed_contract": g.changed_contract,
                     "n_required": len(g.required_contracts),
                     "n_currently_covered": len(g.currently_covered),
                     "n_missing": len(g.missing_coverage), "gap": int(g.gap)})
    _write_csv("coverage_gap.csv", rows, ["case_id", "repo", "claim",
                 "changed_contract", "n_required", "n_currently_covered",
                 "n_missing", "gap"])
    return rows


# ---------------------------------------------------------------------------
# 2. obligation_selection.csv
# ---------------------------------------------------------------------------

def exp_obligation_selection(cases, threshold=0.8):
    rows = []
    for c in cases:
        comp = c["claim"].replace("_completion", "")
        existing = list(EXISTING_VERIFY_SET[c["repo"]].get(comp, []))
        sel = greedy_select(c["repo"], c["gap_obj"], existing, threshold=threshold)
        rows.append({"case_id": c["case_id"], "repo": c["repo"], "claim": c["claim"],
                     "gap": int(c["gap"]), "n_missing": len(c["gap_obj"].missing_coverage),
                     "selected_extra": "|".join(sel.selected_files),
                     "n_selected_extra": len(sel.selected_files),
                     "coverage_achieved": sel.coverage_achieved,
                     "extra_cost": sel.estimated_cost, "threshold": threshold})
    _write_csv("obligation_selection.csv", rows, ["case_id", "repo", "claim", "gap",
                 "n_missing", "selected_extra", "n_selected_extra",
                 "coverage_achieved", "extra_cost", "threshold"])
    return rows


# ---------------------------------------------------------------------------
# 3. detection_cost.csv  (main table + per-case)
# ---------------------------------------------------------------------------

def exp_detection_cost(cases):
    agg, per_case = evaluate_strategies(cases, threshold=0.8)
    # aggregate rows
    out = []
    for name, a in agg.items():
        out.append({"level": "AGGREGATE", "strategy": name, **a})
    for r in per_case:
        out.append({"level": "PER_CASE", **r})
    fields = ["level", "strategy", "detection_rate", "vrr", "avg_test_count",
              "total_runtime", "avg_relative_cost", "case_id", "repo", "claim",
              "true_break", "gap", "existing_detects", "Local_detect",
              "DependencyOnly_detect", "ObligationAware_detect",
              "IntegrationAll_detect", "Local_relcost", "DependencyOnly_relcost",
              "ObligationAware_relcost", "IntegrationAll_relcost",
              "OA_selected_extra", "OA_coverage_achieved", "failure_type"]
    _write_csv("detection_cost.csv", out, fields)
    return agg, per_case


# ---------------------------------------------------------------------------
# 4. threshold_sensitivity.csv
# ---------------------------------------------------------------------------

def exp_threshold_sensitivity(cases):
    rows = []
    for th in (0.6, 0.8, 1.0):
        agg, _ = evaluate_strategies(cases, threshold=th)
        a = agg["ObligationAware"]
        rows.append({"threshold": th, "detection_rate": a["detection_rate"],
                     "vrr": a["vrr"], "avg_test_count": a["avg_test_count"],
                     "avg_relative_cost": a["avg_relative_cost"]})
    _write_csv("threshold_sensitivity.csv", rows, ["threshold", "detection_rate",
                 "vrr", "avg_test_count", "avg_relative_cost"])
    return rows


# ---------------------------------------------------------------------------
# 5. coverage_sensitivity.csv  (existing-verify-set regime)
# ---------------------------------------------------------------------------

def exp_coverage_sensitivity(cases):
    """Re-run the 4 strategies but with the existing verify-set taken from
    LOCAL / MODULE / INTEGRATION regimes (the claim's verify-set breadth).
    ObligationAware always fills the gap from the pool."""
    rows = []
    n_break = sum(1 for c in cases if c["true_break"]) or 1
    integ_cost = {r: _cost(r, POOL_FILES[r]) for r in POOL_FILES}
    for regime in ("LOCAL", "MODULE", "INTEGRATION"):
        for name in ("Local", "DependencyOnly", "ObligationAware", "IntegrationAll"):
            det = 0; relcost = 0.0; ncount = 0
            for c in cases:
                comp = c["claim"].replace("_completion", "")
                existing = list(REPOS_CFG[c["repo"]]["verify"][regime].get(comp, []))
                if name in ("Local", "DependencyOnly"):
                    files = existing
                elif name == "IntegrationAll":
                    files = list(POOL_FILES[c["repo"]])
                else:  # ObligationAware: gap computed against this regime's existing
                    g = compute_gap(c["repo"], c["claim"], c["file"], c["symbol"])
                    # covered_by_files uses pool — recompute covered for this regime
                    cur = set(covered_by_files(c["repo"], existing))
                    missing = [x for x in g.required_contracts if x not in cur]
                    g2 = type(g)(claim=g.claim, repo=g.repo, changed_contract=g.changed_contract,
                                 required_contracts=g.required_contracts,
                                 currently_covered=sorted(cur), missing_coverage=missing,
                                 gap=bool(missing))
                    sel = greedy_select(c["repo"], g2, existing, threshold=0.8)
                    files = list(dict.fromkeys(existing + sel.selected_files))
                if _detects(c, files):
                    det += 1
                ncount += len(files)
                relcost += _cost(c["repo"], files) / max(integ_cost[c["repo"]], 0.001)
            rows.append({"regime": regime, "strategy": name,
                         "detection_rate": round(det / n_break, 4),
                         "avg_test_count": round(ncount / len(cases), 2),
                         "avg_relative_cost": round(relcost / len(cases), 4)})
    _write_csv("coverage_sensitivity.csv", rows, ["regime", "strategy",
                 "detection_rate", "avg_test_count", "avg_relative_cost"])
    return rows


# ---------------------------------------------------------------------------
# 6. failure_analysis.json
# ---------------------------------------------------------------------------

def exp_failure_analysis(per_case):
    types = Counter(r["failure_type"] for r in per_case)
    examples = defaultdict(list)
    for r in per_case:
        if len(examples[r["failure_type"]]) < 3:
            examples[r["failure_type"]].append({
                "case_id": r["case_id"], "repo": r["repo"], "claim": r["claim"],
                "gap": r["gap"], "existing_detects": r["existing_detects"],
                "OA_detect": r["ObligationAware_detect"],
                "Integration_detect": r["IntegrationAll_detect"],
                "OA_selected_extra": r["OA_selected_extra"]})
    out = {"type_counts": dict(types),
           "type_meaning": {
               "A": "gap found + right test selected + detected",
               "B": "gap correct but selected wrong test / missed (integration detects)",
               "C": "no coverage gap (existing covers) but still can't detect (verifier insufficiency)",
               "E": "integration also cannot detect (verification insufficient globally)",
               "NO_BREAK": "no true break",
               "OTHER": "uncategorized"},
           "examples": dict(examples)}
    json.dump(out, open(RESULTS / "failure_analysis.json", "w"), indent=2)
    return out


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run():
    cases = assemble_cases()
    print(f"[v4] cases={len(cases)} gap_positive={sum(1 for c in cases if c['gap'])}", flush=True)
    exp_coverage_gap(cases)
    exp_obligation_selection(cases)
    agg, per_case = exp_detection_cost(cases)
    exp_threshold_sensitivity(cases)
    exp_coverage_sensitivity(cases)
    fa = exp_failure_analysis(per_case)
    # per-repo breakdown
    per_repo = {}
    for r in per_case:
        per_repo.setdefault(r["repo"], {"n": 0, "det_OA": 0, "det_Int": 0,
                                        "det_Local": 0, "n_break": 0})
        per_repo[r["repo"]]["n"] += 1
        per_repo[r["repo"]]["n_break"] += int(r["true_break"])
        per_repo[r["repo"]]["det_OA"] += r["ObligationAware_detect"]
        per_repo[r["repo"]]["det_Int"] += r["IntegrationAll_detect"]
        per_repo[r["repo"]]["det_Local"] += r["Local_detect"]
    summary = {
        "n_cases": len(cases),
        "gap_positive": sum(1 for c in cases if c["gap"]),
        "aggregate": agg,
        "failure_type_counts": fa["type_counts"],
        "per_repo": {k: {**v, "OA_detection_rate": round(v["det_OA"]/max(v["n_break"],1),4),
                          "Int_detection_rate": round(v["det_Int"]/max(v["n_break"],1),4),
                          "Local_detection_rate": round(v["det_Local"]/max(v["n_break"],1),4)}
                     for k, v in per_repo.items()},
    }
    json.dump(summary, open(RESULTS / "summary.json", "w"), indent=2)
    # print headline
    print(f"\n{'Strategy':<18}{'Detection':>11}{'VRR':>8}{'#Tests':>8}{'RelCost':>9}")
    for name, a in agg.items():
        print(f"{name:<18}{a['detection_rate']:>11.3f}{a['vrr']:>8.3f}"
              f"{a['avg_test_count']:>8.2f}{a['avg_relative_cost']:>9.3f}")
    print("\nfailure types:", fa["type_counts"])
    print("per-repo OA/Int/Local detection:",
          {k: (v["OA_detection_rate"], v["Int_detection_rate"], v["Local_detection_rate"])
           for k, v in summary["per_repo"].items()})


if __name__ == "__main__":
    run()