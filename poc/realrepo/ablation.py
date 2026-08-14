"""Ablation: contribution of Static / Dynamic / Semantic / Counterfactual gate.

Variants of the Combined candidate generator:
  full            — trace + dynamic + static + semantic, CF gate on
  no_static       — drop static
  no_dynamic      — drop dynamic
  no_semantic     — drop semantic
  no_trace        — drop trace
  no_cf_gate      — full generator but skip counterfactual gate (accept any fix)

For each variant we report Recall@3, Counterfactual Fix Rate (Top-1),
Regression Failure Rate, Patch Acceptance Rate — averaged over all 2B badcases.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from change_cases import CASES
from common.models import RelType
from badcase_model import (
    load_gstar, delete_edges, make_badcases, gen_combined,
    counterfactual_replay, regression_gate,
)
from experiment_engine import load_oracle
from phase2b import (
    SEEDS, RATIOS, REPOS, _ground_truth_edges, _rank_for_method,
    _recall_at_k, ACCEPT_THRESHOLD,
)


def gen_variant(badcase, g_hat, variant):
    use_static = variant != "no_static"
    use_dynamic = variant != "no_dynamic"
    use_semantic = variant != "no_semantic"
    use_trace = variant != "no_trace"
    cands = gen_combined(badcase, g_hat,
                         use_static=use_static, use_dynamic=use_dynamic,
                         use_semantic=use_semantic, use_trace=use_trace)
    cands = [c for c in cands if c.relation_type == "ARTIFACT->COMPLETION"]
    cands.sort(key=lambda c: -c.confidence)
    return cands


def run():
    oracle = load_oracle(extended=True)
    variants = ["full", "no_static", "no_dynamic", "no_semantic",
                "no_trace", "no_cf_gate"]
    rows = []
    agg = {v: [] for v in variants}
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
                    gt_keys = _ground_truth_edges(deleted, b)
                    for v in variants:
                        ranked = gen_variant(b, g_hat, v)
                        r3 = _recall_at_k(ranked, gt_keys, 3)
                        fix = accept = False
                        regf = 1.0
                        if ranked:
                            cf = counterfactual_replay(b, ranked[0], g_hat)
                            fix = cf["prevent_failure"]
                            if fix and v != "no_cf_gate":
                                reg = regression_gate(ranked[0], g_hat, repo)
                                regf = reg["regression_failure_rate"]
                                accept = (reg["false_invalidation_rate"] <= ACCEPT_THRESHOLD
                                          and regf <= ACCEPT_THRESHOLD)
                            elif fix and v == "no_cf_gate":
                                # accept without checking regression
                                accept = True
                                regf = 0.0
                        rec = {"variant": v, "recall3": r3, "fix": int(fix),
                               "accept": int(accept), "regfail": round(regf, 4)}
                        agg[v].append(rec)
                        rows.append({**rec, "repo": repo, "seed": seed,
                                     "ratio": ratio, "case_id": b["case_id"]})
    Path("results").mkdir(exist_ok=True)
    with open("results/ablation.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variant", "repo", "seed", "ratio", "case_id",
                    "recall3", "fix", "accept", "regfail"])
        for r in rows:
            w.writerow([r["variant"], r["repo"], r["seed"], r["ratio"],
                        r["case_id"], r["recall3"], r["fix"], r["accept"], r["regfail"]])
    summary = {}
    for v, recs in agg.items():
        n = len(recs) or 1
        summary[v] = {
            "n": len(recs),
            "recall3": round(sum(r["recall3"] for r in recs) / n, 4),
            "counterfactual_fix_rate": round(sum(r["fix"] for r in recs) / n, 4),
            "patch_acceptance_rate": round(sum(r["accept"] for r in recs) / n, 4),
            "regression_failure_rate": round(sum(r["regfail"] for r in recs) / n, 4),
        }
    json.dump(summary, open("results/ablation_summary.json", "w"), indent=2)
    return summary


if __name__ == "__main__":
    s = run()
    print(f"{'variant':<14}{'R@3':>6}{'Fix':>6}{'Acc':>6}{'RegF':>6}")
    for v, a in s.items():
        print(f"{v:<14}{a['recall3']:>6.2f}{a['counterfactual_fix_rate']:>6.2f}"
              f"{a['patch_acceptance_rate']:>6.2f}{a['regression_failure_rate']:>6.2f}")