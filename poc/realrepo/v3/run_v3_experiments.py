"""Unified v3 entry:  python3 run_v3_experiments.py [--mode deterministic|agent|full]

Modes:
  deterministic — all deterministic experiments (simulated-agent track); no LLM
  agent         — real-LLM agent trajectory pilot (via coding subagents)
  full          — deterministic + agent
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))         # realrepo
sys.path.insert(0, str(_HERE))                # v3

import v3.experiments as E
from v3.trajectories.simulated_agent import simulate_trajectory, natural_gfc_metrics


def run_deterministic():
    t0 = time.time()
    print("== [1/7] natural_gfc =================", flush=True)
    E.exp_natural_gfc()
    print("== [2/7] invalidation (file vs contract) ==", flush=True)
    E.exp_invalidation()
    print("== [3/7] deletion-ratio recovery =======", flush=True)
    E.exp_deletion_ratio()
    print("== [4/7] coverage sensitivity ==========", flush=True)
    E.exp_coverage_sensitivity()
    print("== [5/7] contract vs file ==============", flush=True)
    E.exp_contract_vs_file()
    print("== [6/7] ablation =======================", flush=True)
    E.exp_ablation()
    print("== [7/7] cost ===========================", flush=True)
    E.exp_cost()
    print(f"\n[deterministic] all experiments done in {time.time()-t0:.1f}s", flush=True)


def run_agent():
    t0 = time.time()
    print("== real-LLM agent trajectory pilot =====", flush=True)
    from v3.trajectories.real_agent import collect_pilot
    summary = collect_pilot(n=6)   # pilot batch (budget-bounded)
    json.dump(summary, open(_HERE / "results" / "real_agent_pilot.json", "w"),
              indent=2, default=str)
    print(f"[agent] pilot done in {time.time()-t0:.1f}s -> results/real_agent_pilot.json",
          flush=True)


def build_summary():
    R = _HERE / "results"
    summary = {}
    for name in ("natural_gfc", "invalidation", "recovery", "coverage",
                 "contract_vs_file", "ablation", "cost"):
        p = R / f"{name}.csv"
        if p.exists():
            summary[name] = p.read_text().strip().splitlines()
    # add the headline calibration facts
    cal = json.load(open(_HERE / "ground_truth" / "calibration.json"))
    summary["calibration_regimes"] = list(cal["regimes"].keys())
    json.dump(summary, open(R / "summary.json", "w"), indent=2, default=str)
    print("[summary] wrote results/summary.json", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["deterministic", "agent", "full"],
                    default="deterministic")
    args = ap.parse_args()
    if args.mode in ("deterministic", "full"):
        run_deterministic()
    if args.mode in ("agent", "full"):
        run_agent()
    build_summary()


if __name__ == "__main__":
    main()