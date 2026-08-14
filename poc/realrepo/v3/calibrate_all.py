"""Calibrate all 50 change cases across LOCAL/MODULE/INTEGRATION regimes,
persist to ground_truth/calibration.json, then build G* + dependency instances."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # realrepo (for v2 common)
sys.path.insert(0, str(_HERE))          # v3 (for engine, contracts)

from engine import (build_change_registry, calibrate_case, REGIMES,
                    build_dependency_instances, build_gstar_contract, is_gfc)


def run():
    cases = build_change_registry()
    Path("v3/ground_truth").mkdir(parents=True, exist_ok=True)
    cal_by_regime = {}
    for regime in REGIMES:
        t0 = time.time()
        rows = []
        for c in cases:
            cal = calibrate_case(c, regime=regime)
            rows.append(cal)
        cal_by_regime[regime] = rows
        print(f"[calibrate] {regime}: {len(rows)} cases in {time.time()-t0:.1f}s",
              flush=True)
    with open("v3/ground_truth/calibration.json", "w") as f:
        json.dump({"regimes": cal_by_regime}, f, indent=2, default=str)

    # build G* + instances at INTEGRATION (broadest completion verify-sets)
    inst = build_dependency_instances(regime="INTEGRATION")
    with open("v3/ground_truth/dependency_instances.json", "w") as f:
        json.dump({"instances": inst}, f, indent=2, default=str)
    gstar = build_gstar_contract(regime="INTEGRATION")
    with open("v3/ground_truth/gstar_contract.json", "w") as f:
        json.dump({"edges": [e.to_dict() for e in gstar]}, f, indent=2)

    # also build G* + instances at LOCAL (where GFC actually occurs) for the
    # contract-vs-file and recovery experiments
    inst_local = build_dependency_instances(regime="LOCAL")
    gstar_local = build_gstar_contract(regime="LOCAL")
    with open("v3/ground_truth/dependency_instances_local.json", "w") as f:
        json.dump({"instances": inst_local}, f, indent=2, default=str)
    with open("v3/ground_truth/gstar_contract_local.json", "w") as f:
        json.dump({"edges": [e.to_dict() for e in gstar_local]}, f, indent=2)

    # headline counts
    n_inst = len(inst) + len(inst_local)
    n_critical = (sum(1 for i in inst if i["gt_should_invalidate"]) +
                  sum(1 for i in inst_local if i["gt_should_invalidate"]))
    gfc_counts = {r: sum(1 for c in cal_by_regime[r] if c.get("applied") and is_gfc(c))
                  for r in REGIMES}
    print(f"instances: {n_inst} (INTEGRATION {len(inst)} + LOCAL {len(inst_local)}) "
          f"| critical(GT-invalidate) edges: {n_critical} "
          f"| G* edges: INTEGRATION {len(gstar)} LOCAL {len(gstar_local)} "
          f"| GFC by regime: {gfc_counts}")


if __name__ == "__main__":
    run()