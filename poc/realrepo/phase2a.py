"""Phase 2A: precise invalidation across 4 strategies, 16 real change cases.

Strategies:
  - all_downstream : invalidate every non-producer claim on any change
  - static         : invalidate only via G* ARTIFACT->COMPLETION edges, ignore scope
  - freshness      : same as static here (content changed => stale); we treat it
                     as static-without-scope to expose the false-invalidation cost
  - change_aware   : G* edges filtered by change_type scope

Metrics per strategy (micro-averaged over all cases):
  Invalidation Precision, Recall, F1, False Invalidation Rate,
  Missed Invalidation Rate, Revalidation Count.

Global False Completion Rate: fraction of cases where the gate reported
VERIFIED while the calibrated oracle FAILED.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from change_cases import CASES
from experiment_engine import (
    make_world, replay_case, ground_truth_invalidation,
    load_oracle, find_oracle_case, GLOBAL_OK,
)
from common.models import Dependency, RelType, ChangeType, Provenance
from common.classifier import ChangeClassifier
from common.repo_driver import RepoDriver


STRATEGIES = ("all_downstream", "static", "freshness", "change_aware")
DOWNSTREAM_SLOTS = ("agent_b_completion", "testing_completion")


def load_gstar(repo: str) -> list:
    d = json.load(open(f"ground_truth/{repo}/dependencies.json"))
    return [Dependency.from_dict(e) for e in d["edges"]]


def run_case(case: dict, strategy: str, oracle: dict) -> dict:
    repo = case["repo"]
    g = load_gstar(repo)
    world = make_world(repo, g, strategy=strategy)
    oc = find_oracle_case(oracle, repo, case["case_id"])
    # freshness variant: drop scope filtering — emulate "any content change => stale"
    if strategy == "freshness":
        for e in g:
            if e.relation_type == RelType.ARTIFACT_TO_COMPLETION:
                e.scope = frozenset(ChangeType) | {None}
    trace = replay_case(world, case, oc)
    gt = ground_truth_invalidation(case, oc)
    pred = set(trace["invalidated"])
    tp = len(pred & gt)
    fp = len(pred - gt)
    fn = len(gt - pred)
    tn = len(set(DOWNSTREAM_SLOTS) - pred - gt)
    return {**trace, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "gt_invalidated": sorted(gt), "pred_invalidated": sorted(pred)}


def aggregate(rows: list) -> dict:
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    tn = sum(r["tn"] for r in rows)
    reverify = sum(len(r["invalidated"]) for r in rows)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    fir = fp / (fp + tn) if fp + tn else 0.0
    mir = fn / (fn + tp) if fn + tp else 0.0
    gfcr = sum(1 for r in rows if r["global_false_completion"]) / len(rows)
    return {"precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4), "false_invalidation_rate": round(fir, 4),
            "missed_invalidation_rate": round(mir, 4),
            "revalidation_count": reverify,
            "global_false_completion_rate": round(gfcr, 4),
            "n_cases": len(rows)}


def run():
    oracle = load_oracle()
    all_rows = []
    summary = {}
    for strat in STRATEGIES:
        rows = []
        for case in CASES:
            oc = find_oracle_case(oracle, case["repo"], case["case_id"])
            if oc.get("applied") is False:
                continue
            rows.append(run_case(case, strat, oracle))
        agg = aggregate(rows)
        summary[strat] = agg
        all_rows.extend(rows)
    # write CSV
    Path("results").mkdir(exist_ok=True)
    with open("results/phase2a.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy", "case_id", "repo", "change_type",
                    "gt_invalidated", "pred_invalidated", "tp", "fp", "fn",
                    "global_after", "oracle_result", "gfc"])
        for r in all_rows:
            w.writerow([r["strategy"], r["case_id"], r["repo"], r["change_type"],
                        "|".join(r["gt_invalidated"]),
                        "|".join(r["pred_invalidated"]),
                        r["tp"], r["fp"], r["fn"], r["global_after"],
                        r["oracle_result"], int(r["global_false_completion"])])
    json.dump(summary, open("results/phase2a_summary.json", "w"), indent=2)
    return summary, all_rows


if __name__ == "__main__":
    summary, rows = run()
    print(f"{'Strategy':<16}{'Prec':>7}{'Rec':>7}{'F1':>7}{'FIR':>7}{'MIR':>7}"
          f"{'Reverify':>9}{'GFCR':>7}")
    for s, a in summary.items():
        print(f"{s:<16}{a['precision']:>7.2f}{a['recall']:>7.2f}{a['f1']:>7.2f}"
              f"{a['false_invalidation_rate']:>7.2f}{a['missed_invalidation_rate']:>7.2f}"
              f"{a['revalidation_count']:>9}{a['global_false_completion_rate']:>7.2f}")