"""Deterministic simulated-agent trajectory generator + natural-GFC accounting.

A simulated-agent trajectory mimics a FOUR-role long-horizon flow on a repo:
  Coordinator -> Developer A (producer task) -> Developer B (depending task)
              -> Testing -> a LATER task modifies the shared contract
  -> observe whether earlier completions went stale.

Crucially: the *code changes* and *test runs* are REAL (pytest subprocess),
only the change-selection is deterministic (not LLM-driven). Every completion's
PASS/FAIL is a real pytest outcome. Trajectories are labeled:

    trajectory_source = "simulated_agent"

and NEVER conflated with real-LLM trajectories.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))         # v3
sys.path.insert(0, str(_HERE.parent.parent))  # realrepo (v2 common)

from engine import (build_change_registry, calibrate_case, REPOS_CFG,
                    COMPLETIONS, REGIMES, is_gfc, global_status)
from contracts.model import ClaimStatus


def simulate_trajectory(case: dict, regime: str) -> dict:
    """One simulated long-horizon trajectory for `case` at verify regime.

    Steps (all using real calibrated pytest outcomes):
      t1: dev_a completes producer task, verifies against dev_a's verify-set
      t2: dev_b completes depending task, verifies against dev_b's set
      t3: testing verifies against testing's set
      -> global completion asserted from these three local claims
      t4: a LATER change (this case's mutation) modifies the shared contract
      -> check whether t1/t2/t3 claims are stale (their verify-sets now FAIL)
      -> if system does NOT invalidate and gate stays VERIFIED while oracle
         FAILS, that is a Natural Global False Completion.
    """
    cal = calibrate_case(case, regime=regime)
    if not cal.get("applied"):
        return {"case_id": case["case_id"], "applied": False,
                "trajectory_source": "simulated_agent"}

    results = cal["results"]
    # exposure: a completion is "exposed" if its verify-set passed before AND
    # we later modify a contract it (transitively) depends on. Here every
    # completion is exposed to the producer change by construction.
    exposed = [c for c in COMPLETIONS]
    # stale = completion whose verify-set FAILS under the change
    stale = [c for c in COMPLETIONS if results.get(c, {}).get("result") == "FAIL"]
    # system-missed: completions that are stale BUT a naive self-report
    # aggregator (which trusts local claims) still marks VERIFIED.
    # In this simulator the system uses self-report aggregation (no invalidation),
    # so ANY stale completion whose existence the gate ignores counts as missed.
    # Gate after change = VERIFIED iff all three local sets PASS.
    local_all_pass = all(results.get(c, {}).get("result") == "PASS" for c in COMPLETIONS)
    oracle_fail = results.get("oracle", {}).get("result") == "FAIL"
    natural_gfc = local_all_pass and oracle_fail

    return {
        "applied": True,
        "trajectory_source": "simulated_agent",
        "case_id": case["case_id"],
        "repo": case["repo"],
        "symbol": case["symbol"],
        "kind": case["kind"],
        "regime": regime,
        "exposure_count": len(exposed),
        "stale_claim_count": len(stale),
        "stale_claims": stale,
        "missed_stale_count": len(stale) if local_all_pass else 0,
        "global_completion_attempted": True,
        "gate_after_change": "VERIFIED" if local_all_pass else ("FAILED" if stale else "VERIFIED"),
        "oracle_result": results.get("oracle", {}).get("result"),
        "natural_gfc": natural_gfc,
        "results": {k: v["result"] for k, v in results.items()},
    }


def run_simulated_track(regimes=REGIMES) -> dict:
    cases = build_change_registry()
    by_regime = {}
    for regime in regimes:
        trajectories = [simulate_trajectory(c, regime) for c in cases
                        if simulate_trajectory(c, regime).get("applied") is not False]
        # NOTE: simulate is cheap (cached calibration) so double-call is fine
        trajectories = [t for t in trajectories if t.get("applied") is not False]
        by_regime[regime] = trajectories
    return by_regime


def natural_gfc_metrics(trajectories: list) -> dict:
    """Compute Natural StaleClaimRate / MissedStaleRate / GFCR for a set of
    simulated trajectories. Distinct from edge-deletion GFC."""
    n = len(trajectories) or 1
    exposure = sum(t["exposure_count"] for t in trajectories)
    stale = sum(t["stale_claim_count"] for t in trajectories)
    missed = sum(t["missed_stale_count"] for t in trajectories)
    gfc = sum(1 for t in trajectories if t["natural_gfc"])
    attempts = sum(1 for t in trajectories if t.get("global_completion_attempted"))
    return {
        "n_trajectories": len(trajectories),
        "exposure_count": exposure,
        "stale_claim_count": stale,
        "missed_stale_count": missed,
        "gfc_count": gfc,
        "global_completion_attempts": attempts,
        "stale_claim_rate": round(stale / exposure, 4) if exposure else 0.0,
        "missed_stale_rate": round(missed / stale, 4) if stale else 0.0,
        "gfcr": round(gfc / attempts, 4) if attempts else 0.0,
    }


if __name__ == "__main__":
    by = run_simulated_track(regimes=["LOCAL"])
    m = natural_gfc_metrics(by["LOCAL"])
    print("simulated LOCAL:", m)