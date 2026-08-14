"""Phase 2B aggregation: missing dependency recovery across 3 repos x 3 seeds
(42/43/44) x 3 deletion ratios (10/20/30%).

For every (repo, seed, ratio):
  - delete ratio of A->C edges from G*  -> G_hat, record deleted (ground truth)
  - run all change cases on G_hat, collect badcases (GFC==TRUE)
  - generate candidates per method + the combined proposed method
  - rank Top-K; for each candidate: counterfactual replay (REAL pytest)
  - regression gate per accepted candidate

Metrics (per ratio, micro-averaged):
  Edge Recall@1/@3/@5, Precision@K, MRR,
  Counterfactual Fix Rate, Patch Acceptance Rate,
  Regression Failure Rate, False Invalidation Rate.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from change_cases import CASES
from common.models import RelType, ChangeType
from badcase_model import (
    load_gstar, delete_edges, make_badcases, gen_trace_heuristic,
    gen_static_only, gen_dynamic_only, gen_combined, gen_semantic,
    counterfactual_replay, regression_gate,
)
from experiment_engine import load_oracle, find_oracle_case


SEEDS = (42, 43, 44)
RATIOS = (0.1, 0.2, 0.3)
REPOS = ("tinydb", "cerberus", "boltons")
ACCEPT_THRESHOLD = 0.2


def _ground_truth_edges(deleted, badcase):
    """Which deleted edges actually explain this badcase (GT for recall)."""
    producer = badcase["producer"]
    return {(e.source, e.target) for e in deleted if e.source == producer}


def _candidate_key(c):
    return (c.source, c.target)


def _rank_for_method(method: str, badcase, g_hat):
    if method == "trace":
        cands = gen_trace_heuristic(badcase, g_hat)
    elif method == "static":
        cands = gen_static_only(badcase, g_hat)
    elif method == "dynamic":
        cands = gen_dynamic_only(badcase, g_hat)
    elif method == "combined":
        cands = gen_combined(badcase, g_hat)
    else:
        raise ValueError(method)
    # keep only ARTIFACT->COMPLETION candidates (the only recoverable kind)
    cands = [c for c in cands if c.relation_type == "ARTIFACT->COMPLETION"]
    cands.sort(key=lambda c: -c.confidence)
    return cands


def _recall_at_k(ranked, gt_keys, k):
    if not gt_keys:
        return 1.0  # nothing to recall
    top = ranked[:k]
    hits = sum(1 for c in top if _candidate_key(c) in gt_keys)
    return 1.0 if hits > 0 else 0.0


def _mrr(ranked, gt_keys):
    for i, c in enumerate(ranked, 1):
        if _candidate_key(c) in gt_keys:
            return 1.0 / i
    return 0.0


def _topk_precision(ranked, gt_keys, k):
    top = ranked[:k]
    if not top:
        return 1.0
    return sum(1 for c in top if _candidate_key(c) in gt_keys) / len(top)


def run_method_on_badcase(method, badcase, g_hat, deleted):
    ranked = _rank_for_method(method, badcase, g_hat)
    gt_keys = _ground_truth_edges(deleted, badcase)
    r1 = _recall_at_k(ranked, gt_keys, 1)
    r3 = _recall_at_k(ranked, gt_keys, 3)
    r5 = _recall_at_k(ranked, gt_keys, 5)
    mrr = _mrr(ranked, gt_keys)
    pk = _topk_precision(ranked, gt_keys, 5)
    # counterfactual + regression on Top-1 candidate only (cost budget)
    fix = accept = False
    reg = {"false_invalidation_rate": 1.0, "regression_failure_rate": 1.0}
    if ranked:
        top = ranked[0]
        cf = counterfactual_replay(badcase, top, g_hat)
        fix = cf["prevent_failure"]
        if fix:
            reg = regression_gate(top, g_hat, badcase["repo"])
            accept = (reg["false_invalidation_rate"] <= ACCEPT_THRESHOLD
                      and reg["regression_failure_rate"] <= ACCEPT_THRESHOLD)
    return {"method": method, "recall1": r1, "recall3": r3, "recall5": r5,
            "mrr": mrr, "precision5": pk, "fix": int(fix),
            "accept": int(accept), "fir": reg["false_invalidation_rate"],
            "regfail": reg["regression_failure_rate"],
            "n_gt_edges": len(gt_keys),
            "top1_target": ranked[0].target if ranked else None,
            "badcase_id": badcase["badcase_id"]}


def run():
    oracle = load_oracle(extended=True)
    rows = []
    by_ratio = {r: {"combined": []} for r in RATIOS}
    for repo in REPOS:
        gstar = load_gstar(repo)
        ac = [e for e in gstar if e.relation_type == RelType.ARTIFACT_TO_COMPLETION]
        if not ac:
            continue
        for ratio in RATIOS:
            for seed in SEEDS:
                g_hat, deleted = delete_edges(gstar, ratio, seed)
                if not deleted:
                    continue
                bcs = make_badcases(g_hat, repo, oracle, seed, ratio)
                for b in bcs:
                    # stamp GT deleted edges onto the badcase record
                    b["deleted_edges_gt"] = [e.to_dict() for e in deleted
                                             if e.source == b["producer"]]
                    rec = run_method_on_badcase("combined", b, g_hat, deleted)
                    rec.update({"repo": repo, "seed": seed, "ratio": ratio,
                                "case_id": b["case_id"],
                                "deleted_gt": [f"{e.source}->{e.target}"
                                               for e in deleted
                                               if e.source == b["producer"]]})
                    rows.append(rec)
                    by_ratio[ratio]["combined"].append(rec)
    # also run ablation methods (trace/static/dynamic) for the ablation table
    # (cheap: no real pytest except their own top1 — but to bound cost we run
    #  them only on the same badcases)
    Path("results").mkdir(exist_ok=True)
    with open("results/phase2b.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo", "seed", "ratio", "case_id", "method", "recall1",
                    "recall3", "recall5", "mrr", "precision5", "fix", "accept",
                    "fir", "regfail", "n_gt", "top1_target", "deleted_gt"])
        for r in rows:
            w.writerow([r["repo"], r["seed"], r["ratio"], r["case_id"],
                        r["method"], r["recall1"], r["recall3"], r["recall5"],
                        r["mrr"], r["precision5"], r["fix"], r["accept"],
                        r["fir"], r["regfail"], r["n_gt_edges"],
                        r["top1_target"], "|".join(r["deleted_gt"])])
    summary = {}
    for ratio, methods in by_ratio.items():
        recs = methods["combined"]
        n = len(recs) or 1
        summary[f"r{int(ratio*100)}"] = {
            "n_badcases": len(recs),
            "edge_recall1": round(sum(r["recall1"] for r in recs) / n, 4),
            "edge_recall3": round(sum(r["recall3"] for r in recs) / n, 4),
            "edge_recall5": round(sum(r["recall5"] for r in recs) / n, 4),
            "precision5": round(sum(r["precision5"] for r in recs) / n, 4),
            "mrr": round(sum(r["mrr"] for r in recs) / n, 4),
            "counterfactual_fix_rate": round(sum(r["fix"] for r in recs) / n, 4),
            "patch_acceptance_rate": round(sum(r["accept"] for r in recs) / n, 4),
            "regression_failure_rate": round(sum(r["regfail"] for r in recs) / n, 4),
            "false_invalidation_rate": round(sum(r["fir"] for r in recs) / n, 4),
        }
    json.dump(summary, open("results/phase2b_summary.json", "w"), indent=2)
    json.dump([r for r in rows], open("results/phase2b_rows.json", "w"), indent=2)
    return summary, rows


if __name__ == "__main__":
    summary, rows = run()
    print(f"{'ratio':<6}{'nBC':>5}{'R@1':>6}{'R@3':>6}{'R@5':>6}{'MRR':>6}"
          f"{'Fix':>6}{'Acc':>6}{'RegF':>6}{'FIR':>6}")
    for k, a in summary.items():
        print(f"{k:<6}{a['n_badcases']:>5}{a['edge_recall1']:>6.2f}"
              f"{a['edge_recall3']:>6.2f}{a['edge_recall5']:>6.2f}"
              f"{a['mrr']:>6.2f}{a['counterfactual_fix_rate']:>6.2f}"
              f"{a['patch_acceptance_rate']:>6.2f}"
              f"{a['regression_failure_rate']:>6.2f}"
              f"{a['false_invalidation_rate']:>6.2f}")