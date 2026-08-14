"""v3 experiments: produce all required CSVs from calibrated data.

Replay against a graph uses the calibrated (REAL pytest, cached) per-completion
verify-set outcome as the revalidation oracle. This is deterministic and fast;
counterfactual confirmation of the TOP candidate is re-run with real pytest in
recovery.py for the accepted patches.
"""
from __future__ import annotations

import csv
import json
import random
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))         # v3
sys.path.insert(0, str(_HERE.parent.parent))  # realrepo

from contracts.model import ChangeType, DependencyEdge, Granularity
from engine import (build_change_registry, calibrate_case, REPOS_CFG,
                    COMPLETIONS, REGIMES, is_gfc, gt_invalidation,
                    build_gstar_contract, GLOBAL_OK, GLOBAL_NOT_READY, GLOBAL_FAILED)
from invalidation import invalidate, ALL_STRATEGIES
from recovery import (rank_hybrid, counterfactual_replay, regression_gate,
                      gen_trace_candidates, gen_static_candidates,
                      gen_dynamic_candidates, gen_semantic_candidates)
from trajectories.simulated_agent import simulate_trajectory, natural_gfc_metrics

RESULTS = Path(__file__).resolve().parents[1] / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
DOWNSTREAM = ("dev_b_completion", "testing_completion")

_CT = {"BODY_ONLY": ChangeType.NON_SEMANTIC, "ADD_OPTIONAL_PARAM": ChangeType.COMPATIBLE,
       "CHANGE_RETURN_TYPE": ChangeType.POTENTIALLY_BREAKING,
       "ADD_REQUIRED_PARAM": ChangeType.BREAKING}


def _ct(kind):
    return _CT[kind]


# ---------------------------------------------------------------------------
# replay against a graph (deterministic, uses calibrated verify outcomes)
# ---------------------------------------------------------------------------

def replay_with_graph(case, graph, regime):
    """Start all completions VERIFIED; invalidate per graph (change-aware,
    scope-matching); revalidate invalidated ones to their calibrated verify-set
    result. Return statuses, gate, gfc."""
    cal = calibrate_case(case, regime=regime)
    if not cal.get("applied"):
        return None
    ct = _ct(case["kind"])
    producer_contract = f"{case['file']}::FUNCTION_SIGNATURE::{case['symbol']}"
    # graph edges whose source matches EITHER the contract OR the file
    targets = set()
    for e in graph:
        if e.source not in (producer_contract, case["file"]):
            continue
        if e.relation_type not in ("CONTRACT->COMPLETION", "ARTIFACT->COMPLETION"):
            continue
        if ct not in e.scope:
            continue
        targets.add(e.target)
    statuses = {c: "VERIFIED" for c in COMPLETIONS}
    for t in targets:
        # revalidate: status = calibrated verify-set result
        res = cal["results"].get(t, {}).get("result", "FAIL")
        statuses[t] = "FAILED" if res == "FAIL" else "VERIFIED"
    gate = _gate(statuses)
    gfc = (gate == "VERIFIED" and cal["results"].get("oracle", {}).get("result") == "FAIL")
    return {"statuses": statuses, "gate": gate, "gfc": gfc,
            "oracle": cal["results"]["oracle"]["result"], "invalidated": sorted(targets)}


def _gate(statuses):
    if any(v == "FAILED" for v in statuses.values()):
        return "FAILED"
    if any(v in ("STALE", "PENDING") for v in statuses.values()):
        return "NOT_READY"
    return "VERIFIED"


def load_gstar(regime):
    fn = f"v3/ground_truth/gstar_contract.json" if regime == "INTEGRATION" \
        else f"v3/ground_truth/gstar_contract_local.json"
    d = json.load(open(fn))
    return [DependencyEdge.from_dict(e) for e in d["edges"]]


# ===========================================================================
# Experiment 1: natural GFC incidence (simulated-agent track)
# ===========================================================================

def exp_natural_gfc():
    cases = build_change_registry()
    rows = []
    for regime in REGIMES:
        per_repo = {}
        for c in cases:
            t = simulate_trajectory(c, regime)
            if not t.get("applied"):
                continue
            t["trajectory_source"] = "simulated_agent"
            per_repo.setdefault(c["repo"], []).append(t)
        for repo, ts in per_repo.items():
            m = natural_gfc_metrics(ts)
            rows.append({"trajectory_source": "simulated_agent", "regime": regime,
                         "repo": repo, **m})
    _write_csv("natural_gfc.csv", rows, ["trajectory_source", "regime", "repo",
                 "n_trajectories", "exposure_count", "stale_claim_count",
                 "missed_stale_count", "gfc_count", "stale_claim_rate",
                 "missed_stale_rate", "gfcr"])
    return rows


# ===========================================================================
# Experiment 2: coverage sensitivity (GFC gradient + recovery fix rate)
# ===========================================================================

def exp_coverage_sensitivity():
    cases = build_change_registry()
    rows = []
    for regime in REGIMES:
        g_gstar = load_gstar(regime if regime == "INTEGRATION" else "LOCAL")
        for repo in REPOS_CFG:
            rcases = [c for c in cases if c["repo"] == repo]
            # GFCR with COMPLETE graph (verification sufficiency)
            gfcs_full = badcases_full = 0
            verify_cost = 0.0
            for c in rcases:
                r = replay_with_graph(c, g_gstar, regime)
                if r is None:
                    continue
                gfcs_full += int(r["gfc"])
                cal = calibrate_case(c, regime=regime)
                verify_cost += sum(v["duration_s"] for k, v in cal["results"].items()
                                   if k != "oracle")
            # recovery fix rate over the regime's own deletion badcases (r=30,seed=42)
            fix = recovered = nbc = 0
            g_hat, deleted = delete_edges(g_gstar, 0.30, 42)
            for c in rcases:
                bc = make_badcase(c, g_hat, regime, deleted)
                if bc is None:
                    continue
                nbc += 1
                ranked, ties = rank_hybrid(bc, g_hat)
                if ranked:
                    cf = counterfactual_replay(bc, ranked[0], g_hat, regime=regime)
                    if cf["prevented_gate"]:
                        fix += 1
            rows.append({"regime": regime, "repo": repo,
                         "gfcr_full_graph": round(gfcs_full / max(1, len(rcases)), 4),
                         "n_badcases": nbc, "recovered": fix,
                         "recovery_fix_rate": round(fix / max(1, nbc), 4),
                         "verify_runtime_s": round(verify_cost, 3)})
    _write_csv("coverage.csv", rows, ["regime", "repo", "gfcr_full_graph",
                 "n_badcases", "recovered", "recovery_fix_rate", "verify_runtime_s"])
    return rows


# ===========================================================================
# Experiment 3: invalidation strategies — file vs contract (precision/recall)
# ===========================================================================

def exp_invalidation():
    cases = build_change_registry()
    strategies = [("all_downstream", "file"),
                  ("static_file", "file"), ("freshness", "file"),
                  ("change_aware_file", "file"),
                  ("static_contract", "contract"), ("change_aware_contract", "contract")]
    rows = []
    for strat, level in strategies:
        tp = fp = fn = tn = reverify = 0
        for regime in ("LOCAL",):
            g_gstar = load_gstar("LOCAL")
            for c in cases:
                cal = calibrate_case(c, regime=regime)
                if not cal.get("applied"):
                    continue
                ct = _ct(c["kind"])
                src = c["file"] if level == "file" else f"{c['file']}::FUNCTION_SIGNATURE::{c['symbol']}"
                pred = invalidate(strat, g_gstar, src, ct)
                pred &= set(DOWNSTREAM)
                gt = gt_invalidation(cal, regime) & set(DOWNSTREAM)
                tp += len(pred & gt); fp += len(pred - gt)
                fn += len(gt - pred); tn += len(set(DOWNSTREAM) - pred - gt)
                reverify += len(pred)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
        fir = fp / (fp + tn) if fp + tn else 0
        rows.append({"strategy": strat, "granularity": level,
                     "precision": round(prec, 4), "recall": round(rec, 4),
                     "f1": round(f1, 4), "false_invalidation_rate": round(fir, 4),
                     "revalidation_count": reverify})
    _write_csv("invalidation.csv", rows, ["strategy", "granularity", "precision",
                 "recall", "f1", "false_invalidation_rate", "revalidation_count"])
    return rows


# ===========================================================================
# Experiment 4: deletion-ratio recovery (G* contract, INTEGRATION where ≥50 edges)
# ===========================================================================

PRIORITY_RELS = ("CONTRACT->COMPLETION", "ARTIFACT->COMPLETION")


def delete_edges(g_star, ratio, seed):
    rng = random.Random(seed)
    targets = [e for e in g_star if e.relation_type in PRIORITY_RELS]
    n_del = max(1, round(len(targets) * ratio)) if targets else 0
    n_del = min(n_del, len(targets))
    perm = sorted(targets, key=lambda e: rng.random())
    deleted = perm[:n_del]
    keys = {e.key() for e in deleted}
    g_hat = [e for e in g_star if e.key() not in keys]
    return g_hat, deleted


def make_badcase(case, g_hat, regime, deleted):
    """A badcase exists iff replay on G_hat produces GFC that G_star would NOT."""
    cal = calibrate_case(case, regime=regime)
    if not cal.get("applied"):
        return None
    r_hat = replay_with_graph(case, g_hat, regime)
    if r_hat is None or not r_hat["gfc"]:
        return None
    return {"case_id": case["case_id"], "repo": case["repo"], "file": case["file"],
            "symbol": case["symbol"], "kind": case["kind"],
            "change_type": _ct(case["kind"]).value, "regime": regime,
            "deleted_gt_edges": [e.key() for e in deleted
                                 if e.source.startswith(case["file"]) or e.source == case["file"]],
            "g_hat": g_hat, "trajectory_source": "simulated_agent"}


def exp_deletion_ratio():
    ratios = (0.05, 0.10, 0.20, 0.30)
    seeds = (42, 43, 44, 45, 46)
    cases = build_change_registry()
    rows = []
    for regime in ("LOCAL", "INTEGRATION"):
        g_star = load_gstar(regime)
        n_edges = sum(1 for e in g_star if e.relation_type in PRIORITY_RELS)
        insufficient = n_edges < 50
        for ratio in ratios:
            agg = {m: 0.0 for m in ("recall1", "recall3", "recall5", "mrr", "fix",
                                    "accept", "regfail", "fir")}
            nbc = 0
            for seed in seeds:
                g_hat, deleted = delete_edges(g_star, ratio, seed)
                for c in cases:
                    bc = make_badcase(c, g_hat, regime, deleted)
                    if bc is None:
                        continue
                    nbc += 1
                    gt_keys = {(e.source, e.target, e.relation_type, e.granularity.value)
                               for e in deleted if e.target in DOWNSTREAM
                               and (e.source.startswith(c["file"]) or e.source == c["file"])}
                    ranked, ties = rank_hybrid(bc, g_hat)
                    agg["recall1"] += _recall_k(ranked, gt_keys, 1)
                    agg["recall3"] += _recall_k(ranked, gt_keys, 3)
                    agg["recall5"] += _recall_k(ranked, gt_keys, 5)
                    agg["mrr"] += _mrr(ranked, gt_keys)
                    fix = accept = 0; regfail = fir = 0.0; det = 0
                    if ranked:
                        cf = counterfactual_replay(bc, ranked[0], g_hat, regime=regime)
                        fix = int(cf["prevented_gate"])      # conservative-gate GFC prevention
                        det = int(cf["detected"])            # verifier actually caught the break
                        if fix:
                            rg = regression_gate(ranked[0], g_hat, c["repo"], regime)
                            fir = rg["false_invalidation_rate"]
                            regfail = rg["regression_failure_rate"]
                            accept = int(fir <= 0.2 and regfail <= 0.2)
                    agg["fix"] += fix; agg["accept"] += accept
                    agg["regfail"] += regfail; agg["fir"] += fir
                    agg.setdefault("det", 0.0); agg["det"] += det
            n = nbc or 1
            rows.append({"regime": regime, "ratio": ratio, "n_edges": n_edges,
                         "insufficient_graph": int(insufficient),
                         "n_badcases": nbc,
                         "recall1": round(agg["recall1"] / n, 4),
                         "recall3": round(agg["recall3"] / n, 4),
                         "recall5": round(agg["recall5"] / n, 4),
                         "mrr": round(agg["mrr"] / n, 4),
                         "gfc_prevention_rate": round(agg["fix"] / n, 4),
                         "detection_rate": round(agg["det"] / n, 4),
                         "patch_acceptance_rate": round(agg["accept"] / n, 4),
                         "regression_failure_rate": round(agg["regfail"] / n, 4),
                         "false_invalidation_rate": round(agg["fir"] / n, 4)})
    _write_csv("recovery.csv", rows, ["regime", "ratio", "n_edges", "insufficient_graph",
                 "n_badcases", "recall1", "recall3", "recall5", "mrr",
                 "gfc_prevention_rate", "detection_rate", "patch_acceptance_rate",
                 "regression_failure_rate", "false_invalidation_rate"])
    return rows


def _cand_key(c):
    return (c.source, c.target, c.relation_type, c.granularity.value
            if hasattr(c.granularity, "value") else c.granularity)


def _recall_k(ranked, gt_keys, k):
    if not gt_keys:
        return 1.0
    return 1.0 if any(_cand_key(c) in gt_keys for c in ranked[:k]) else 0.0


def _mrr(ranked, gt_keys):
    for i, c in enumerate(ranked, 1):
        if _cand_key(c) in gt_keys:
            return 1.0 / i
    return 0.0


# ===========================================================================
# Experiment 5: contract vs file candidate granularity
# ===========================================================================

def exp_contract_vs_file():
    g_star = load_gstar("LOCAL")
    cases = build_change_registry()
    rows = []
    for granularity in ("file", "contract"):
        tp = fp = fn = tn = reverify = 0
        fix = accept = 0
        nbc = 0
        g_hat, deleted = delete_edges(g_star, 0.30, 42)
        for c in cases:
            bc = make_badcase(c, g_hat, "LOCAL", deleted)
            if bc is None:
                continue
            nbc += 1
            ranked, ties = rank_hybrid(bc, g_hat)
            # restrict candidate granularity for the comparison
            if granularity == "file":
                ranked = [x for x in ranked if x.granularity.value in ("FILE", "SYMBOL")] or ranked
            else:
                ranked = [x for x in ranked if x.granularity.value == "CONTRACT"] or ranked
            gt_keys = {(e.source, e.target, e.relation_type, e.granularity.value)
                       for e in deleted if e.target in DOWNSTREAM
                       and (e.source.startswith(c["file"]) or e.source == c["file"])}
            rec3 = _recall_k(ranked, gt_keys, 3)
            cf_ok = rg_ok = 0
            if ranked:
                cf = counterfactual_replay(bc, ranked[0], g_hat, "LOCAL")
                cf_ok = int(cf["prevented_gate"])
                if cf_ok:
                    rg = regression_gate(ranked[0], g_hat, c["repo"], "LOCAL")
                    rg_ok = int(rg["false_invalidation_rate"] <= 0.2
                                and rg["regression_failure_rate"] <= 0.2)
            rows.append({"granularity": granularity, "case": c["case_id"],
                         "recall3": rec3, "cf_fix": cf_ok, "patch_accept": rg_ok})
            fix += cf_ok; accept += rg_ok
        n = nbc or 1
        rows.append({"granularity": granularity + "_TOTAL", "case": "AGG",
                     "recall3": round(sum(r["recall3"] for r in rows
                                          if r["granularity"] == granularity) / n, 4),
                     "cf_fix": fix, "patch_accept": accept, "n_badcases": nbc})
    _write_csv("contract_vs_file.csv", rows, ["granularity", "case", "recall3",
                 "cf_fix", "patch_accept", "n_badcases"])
    return rows


# ===========================================================================
# Experiment 6: ablation (with explicit TieRate)
# ===========================================================================

def exp_ablation():
    g_star = load_gstar("LOCAL")
    cases = build_change_registry()
    variants = ["full", "no_static", "no_dynamic", "no_semantic", "no_trace",
                "no_cf_gate", "no_regression_gate"]
    rows = []
    g_hat, deleted = delete_edges(g_star, 0.30, 42)
    for var in variants:
        agg = {"recall3": 0.0, "mrr": 0.0, "fix": 0, "accept": 0, "regfail": 0.0, "ties": 0}
        nbc = 0
        for c in cases:
            bc = make_badcase(c, g_hat, "LOCAL", deleted)
            if bc is None:
                continue
            nbc += 1
            ranked, ties = rank_hybrid(bc, g_hat, ablation=var if var in
                                       ("full", "no_static", "no_dynamic",
                                        "no_semantic", "no_trace") else "full")
            gt_keys = {(e.source, e.target, e.relation_type, e.granularity.value)
                       for e in deleted if e.target in DOWNSTREAM
                       and (e.source.startswith(c["file"]) or e.source == c["file"])}
            agg["recall3"] += _recall_k(ranked, gt_keys, 3)
            agg["mrr"] += _mrr(ranked, gt_keys)
            agg["ties"] += int(bool(ties["tie_at_top"]))
            fix = accept = 0; regfail = 0.0
            if ranked:
                cf = counterfactual_replay(bc, ranked[0], g_hat, "LOCAL")
                fix = int(cf["prevented_gate"])
                if fix and var != "no_cf_gate":
                    rg = regression_gate(ranked[0], g_hat, c["repo"], "LOCAL")
                    regfail = rg["regression_failure_rate"]
                    if var != "no_regression_gate":
                        accept = int(rg["false_invalidation_rate"] <= 0.2
                                     and rg["regression_failure_rate"] <= 0.2)
                elif fix and var == "no_cf_gate":
                    accept = 1
                elif fix and var == "no_regression_gate":
                    accept = 1  # accept without regression check
            agg["fix"] += fix; agg["accept"] += accept; agg["regfail"] += regfail
        n = nbc or 1
        rows.append({"variant": var, "n_badcases": nbc,
                     "recall3": round(agg["recall3"] / n, 4),
                     "mrr": round(agg["mrr"] / n, 4),
                     "cf_fix_rate": round(agg["fix"] / n, 4),
                     "patch_acceptance_rate": round(agg["accept"] / n, 4),
                     "regression_failure_rate": round(agg["regfail"] / n, 4),
                     "tie_rate": round(agg["ties"] / n, 4)})
    _write_csv("ablation.csv", rows, ["variant", "n_badcases", "recall3", "mrr",
                 "cf_fix_rate", "patch_acceptance_rate", "regression_failure_rate",
                 "tie_rate"])
    return rows


# ===========================================================================
# Experiment 7: cost
# ===========================================================================

def exp_cost(call_counts=None):
    rows = []
    # calibration cost (from log)
    rows.append({"component": "calibration", "test_runtime_s": "~487",
                 "n_pytest_runs": "≈600", "llm_tokens": 0, "agent_calls": 0,
                 "note": "50 cases x 3 regimes real pytest (one-time, cached)"})
    if call_counts:
        rows.append(call_counts)
    _write_csv("cost.csv", rows, ["component", "test_runtime_s", "n_pytest_runs",
                 "llm_tokens", "agent_calls", "note"])
    return rows


# ===========================================================================
# helpers
# ===========================================================================

def _write_csv(name, rows, fields):
    with open(RESULTS / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    t0 = time.time()
    print("== natural_gfc =="); exp_natural_gfc()
    print("== invalidation =="); exp_invalidation()
    print("== deletion_ratio =="); exp_deletion_ratio()
    print("== coverage =="); exp_coverage_sensitivity()
    print("== contract_vs_file =="); exp_contract_vs_file()
    print("== ablation =="); exp_ablation()
    print("== cost =="); exp_cost()
    print(f"done in {time.time()-t0:.1f}s")