"""Coverage sensitivity: does a stronger Testing Agent erase the problem?

We use the SAME change cases but two testing-coverage regimes on tinydb:
  LOW    = base config   (testing verify-set = test_operations.py only)
  MEDIUM = base + test_tables.py added to testing
  HIGH   = extended config (testing = test_operations + test_tables)

For each regime we measure:
  - Global False Completion Rate (GFCR) under the *complete* G*_regime graph
  - Missing Dependency Recovery Rate (over the same deletion badcases that
    still occur — when none occur, recovery rate is undefined / reported as N/A)

This directly answers: "when the Testing Agent is strong, does the method
still add value?"
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from change_cases import CASES
from common.models import Dependency, RelType, ChangeType, Provenance
from experiment_engine import (
    make_world, replay_case, load_oracle, find_oracle_case, GLOBAL_OK,
)
from badcase_model import (
    load_gstar, delete_edges, make_badcases, gen_combined,
    counterfactual_replay,
)


REGIMES = [
    ("LOW", "base", "ground_truth/base"),
    ("HIGH", "extended", "ground_truth/extended"),
]


def _gfcr(graph, oracle, repo, extended):
    n = gfcs = 0
    for case in CASES:
        if case["repo"] != repo:
            continue
        oc = find_oracle_case(oracle, case["repo"], case["case_id"])
        if oc.get("applied") is False:
            continue
        world = make_world(repo, graph, strategy="change_aware")
        trace = replay_case(world, case, oc)
        n += 1
        if trace["global_false_completion"]:
            gfcs += 1
    return gfcs, n


def run():
    repo = "tinydb"
    rows = []
    summary = {}
    for label, oracle_label, gt_dir in REGIMES:
        oracle = load_oracle(extended=(oracle_label == "extended"))
        gstar = [Dependency.from_dict(e) for e in
                 json.load(open(f"{gt_dir}/{repo}/dependencies.json"))["edges"]]
        gfcs, n = _gfcr(gstar, oracle, repo, oracle_label == "extended")
        gfcr = round(gfcs / n, 4) if n else 0.0

        # recovery over deletion badcases at r30 seed42
        g_hat, deleted = delete_edges(gstar, 0.3, 42)
        bcs = make_badcases(g_hat, repo, oracle, 42, 0.3)
        recovered = 0
        for b in bcs:
            ranked = [c for c in gen_combined(b, g_hat)
                      if c.relation_type == "ARTIFACT->COMPLETION"]
            ranked.sort(key=lambda c: -c.confidence)
            if ranked:
                cf = counterfactual_replay(b, ranked[0], g_hat)
                if cf["prevent_failure"]:
                    recovered += 1
        recovery_rate = round(recovered / len(bcs), 4) if bcs else None
        rows.append({"regime": label, "config": oracle_label,
                     "gfcr": gfcr, "n_cases": n,
                     "n_badcases": len(bcs),
                     "recovered": recovered,
                     "recovery_rate": recovery_rate})
        summary[label] = rows[-1]

    Path("results").mkdir(exist_ok=True)
    with open("results/coverage.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["regime", "config", "gfcr", "n_cases",
                    "n_badcases", "recovered", "recovery_rate"])
        for r in rows:
            w.writerow([r["regime"], r["config"], r["gfcr"], r["n_cases"],
                        r["n_badcases"], r["recovered"],
                        r["recovery_rate"] if r["recovery_rate"] is not None else "NA"])
    json.dump(summary, open("results/coverage_summary.json", "w"), indent=2)
    return summary


if __name__ == "__main__":
    s = run()
    print(f"{'regime':<8}{'GFCR':>6}{'nCases':>8}{'nBC':>5}{'recovered':>10}{'recRate':>9}")
    for k, r in s.items():
        rr = r["recovery_rate"] if r["recovery_rate"] is not None else "NA"
        print(f"{k:<8}{r['gfcr']:>6.2f}{r['n_cases']:>8}{r['n_badcases']:>5}"
              f"{r['recovered']:>10}{str(rr):>9}")