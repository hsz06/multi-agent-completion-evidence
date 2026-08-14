"""Unified v4 entry:  python3 run_verification_obligation_poc.py [--selector deterministic|llm]

Deterministic by default. --selector llm reserved for future LLM API; if no API
is configured the run prints LLM_SELECTOR_NOT_AVAILABLE and continues with the
deterministic selector (PoC never blocks on a missing API).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selector", choices=["deterministic", "llm"],
                    default="deterministic")
    args = ap.parse_args()
    if args.selector == "llm":
        print("LLM_SELECTOR_NOT_AVAILABLE — no LLM API configured; "
              "falling back to deterministic selector.", flush=True)
    t0 = time.time()
    print("== building verification pool (pristine line-level coverage) ==", flush=True)
    from obligation.pool import dump_pools
    sizes = dump_pools()
    print("pool sizes:", sizes, flush=True)
    print("== running v4 experiments ==", flush=True)
    from v4.experiments import run
    run()
    print(f"\n[done] {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()