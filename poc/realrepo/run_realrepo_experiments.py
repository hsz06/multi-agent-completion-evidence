"""Unified entry: python3 run_realrepo_experiments.py

Runs the full RealRepo-PoC v2 pipeline end-to-end and writes:
  results/summary.json, phase2a.csv, phase2b.csv, ablation.csv, coverage.csv
Plus prints the headline numbers to stdout.

This script assumes oracle calibration has already been run (oracle_calibrated*.json
exist). If not, it runs calibration first (real pytest, ~30s).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _ensure_oracle():
    need = []
    if not (HERE / "oracle_calibrated.json").exists():
        need.append("base")
    if not (HERE / "oracle_calibrated_extended.json").exists():
        need.append("extended")
    if need:
        print("[run] oracle calibration missing -> running real pytest "
              "(this takes ~60s)...", flush=True)
        import oracle_calibration
        oracle_calibration.main()


def main():
    os.chdir(HERE)
    _ensure_oracle()

    print("\n==== building ground truth G* ====", flush=True)
    import ground_truth_graph
    ground_truth_graph.dump_all()

    print("\n==== Phase 2A: precise invalidation ====", flush=True)
    import phase2a
    p2a_summary, p2a_rows = phase2a.run()
    print(f"{'Strategy':<16}{'Prec':>7}{'Rec':>7}{'F1':>7}{'FIR':>7}"
          f"{'MIR':>7}{'Reverify':>9}{'GFCR':>7}")
    for s, a in p2a_summary.items():
        print(f"{s:<16}{a['precision']:>7.2f}{a['recall']:>7.2f}{a['f1']:>7.2f}"
              f"{a['false_invalidation_rate']:>7.2f}"
              f"{a['missed_invalidation_rate']:>7.2f}"
              f"{a['revalidation_count']:>9}{a['global_false_completion_rate']:>7.2f}")

    print("\n==== Phase 2B: missing dependency recovery ====", flush=True)
    import phase2b
    p2b_summary, p2b_rows = phase2b.run()
    print(f"{'ratio':<6}{'nBC':>5}{'R@1':>6}{'R@3':>6}{'R@5':>6}{'MRR':>6}"
          f"{'Fix':>6}{'Acc':>6}{'RegF':>6}{'FIR':>6}")
    for k, a in p2b_summary.items():
        print(f"{k:<6}{a['n_badcases']:>5}{a['edge_recall1']:>6.2f}"
              f"{a['edge_recall3']:>6.2f}{a['edge_recall5']:>6.2f}"
              f"{a['mrr']:>6.2f}{a['counterfactual_fix_rate']:>6.2f}"
              f"{a['patch_acceptance_rate']:>6.2f}"
              f"{a['regression_failure_rate']:>6.2f}"
              f"{a['false_invalidation_rate']:>6.2f}")

    print("\n==== Ablation ====", flush=True)
    import ablation
    abl = ablation.run()
    print(f"{'variant':<14}{'R@3':>6}{'Fix':>6}{'Acc':>6}{'RegF':>6}")
    for v, a in abl.items():
        print(f"{v:<14}{a['recall3']:>6.2f}{a['counterfactual_fix_rate']:>6.2f}"
              f"{a['patch_acceptance_rate']:>6.2f}"
              f"{a['regression_failure_rate']:>6.2f}")

    print("\n==== Coverage sensitivity (tinydb) ====", flush=True)
    import coverage_sensitivity
    cov = coverage_sensitivity.run()
    print(f"{'regime':<8}{'GFCR':>6}{'nCases':>8}{'nBC':>5}{'recovered':>10}"
          f"{'recRate':>9}")
    for k, r in cov.items():
        rr = r["recovery_rate"] if r["recovery_rate"] is not None else "NA"
        print(f"{k:<8}{r['gfcr']:>6.2f}{r['n_cases']:>8}{r['n_badcases']:>5}"
              f"{r['recovered']:>10}{str(rr):>9}")

    summary = {
        "phase2a": p2a_summary,
        "phase2b": p2b_summary,
        "ablation": abl,
        "coverage_sensitivity": cov,
    }
    json.dump(summary, open("results/summary.json", "w"), indent=2)
    print("\n[run] wrote results/summary.json + *.csv")


if __name__ == "__main__":
    main()