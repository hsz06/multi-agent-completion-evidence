"""Oracle calibration: run every change-case against the REAL pinned repos.

For each case we execute the modified tree through real pytest on the agent
verify sets and the oracle set, record PASS/FAIL per target, then restore the
pristine copy. The result is the deterministic Ground Truth for all v2
experiments — no LLM, no guessing.

Usage:  python3 -m python3_oracle  (from realrepo/)  ->  realrepo/oracle_calibrated.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from change_cases import CASES, REPO_VERIFY_SETS, REPO_VERIFY_SETS_EXTENDED
from common.repo_driver import RepoDriver
from common.classifier import ChangeClassifier, extract_signatures
from common.models import ChangeType


def calibrate(repo_name: str, case: dict, sets: dict) -> dict:
    driver = RepoDriver(repo_name)
    producer = case["producer"]
    orig = driver.read(producer)
    new_src = None
    try:
        new_src = case["transform"](orig)
    except AssertionError as e:
        driver.cleanup()
        return {"case": case["case_id"], "applied": False,
                "error": str(e)[:300], "repo": repo_name}
    assert new_src != orig, "transform produced identical content"
    driver.write(producer, new_src)

    classifier = ChangeClassifier()
    ct = classifier.classify(orig, new_src)
    results = {}
    for set_name, targets in sets.items():
        r = driver.run_pytest(targets)
        results[set_name] = {"result": r["result"], "tests_failed": r["tests_failed"],
                             "duration_s": r["duration_s"]}
    driver.cleanup()
    return {
        "case": case["case_id"],
        "repo": repo_name,
        "producer": producer,
        "note": case["note"],
        "expected_ct": case["ct"],
        "classified_ct": ct.value,
        "results": results,
    }


def _run_config(label: str, sets_map: dict, out_path: str):
    out = {"config": label, "repos": {}, "cases": []}
    for repo in ("tinydb", "cerberus", "boltons"):
        cases = [c for c in CASES if c["repo"] == repo]
        out["repos"][repo] = {"verify_sets": sets_map[repo]}
        for c in cases:
            print(f"[{label}] {c['case_id']} ...", flush=True)
            rec = calibrate(repo, c, sets_map[repo])
            out["cases"].append(rec)
            print(f"    {rec.get('error', rec['results'])}", flush=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {out_path}")


def main():
    _run_config("base", REPO_VERIFY_SETS, "oracle_calibrated.json")
    _run_config("extended", REPO_VERIFY_SETS_EXTENDED,
                "oracle_calibrated_extended.json")


if __name__ == "__main__":
    main()