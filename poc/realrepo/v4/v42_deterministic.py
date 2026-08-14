"""v4.2-A deterministic track: PrivateContract + AssertionTop1 + EarlyStop.

Builds a private-contract-aware coverage map (obligation covers a contract iff
it executes the symbol body, public OR private). The selector then reasons
about the changed file's full contract surface (public + private), which can
surface candidates that v4.1's public-only gap missed.

Anti-leakage: private coverage is pristine (pre-change) line-level; never
reads the held-out oracle. 56-sample fair comparison with v4.1.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))

from engine import REPOS_CFG, REPOS_DIR, build_change_registry
from config import POOL_FILES, EXISTING_VERIFY_SET
from obligation.pool import build_pool
from obligation.coverage import (compute_gap, candidates_for_gap,
                                  covered_by_files, required_contracts)
from obligation.line_cov import coverage_lines, covers_contract
from private_contract import extract_private_contracts, PrivateContractNode
from assertion import file_sensitivity
from assertion_selector import assertion_aware_select
from strategies import (assemble_cases, _file_fails, _cost, _detects,
                        ORACLE, BASELINE, DOWNSTREAM)

RESULTS = _HERE / "results"


def _private_contracts_for_file(repo: str, artifact: str) -> list:
    return [p for p in extract_private_contracts(repo) if p.file == artifact]


def _pool_covers_private_body(repo: str, testfile: str, artifact: str,
                              priv_symbols: list) -> list:
    """Which private symbols' bodies does `testfile` execute (line-level)?"""
    lm = coverage_lines(repo, testfile)
    out = []
    for p in priv_symbols:
        start, end = p.line_range
        executed = lm.get(artifact, set())
        if any((start + 1) <= e <= end for e in executed):
            out.append(p.symbol)
    return out


def dump_private_contracts():
    """Emit private_contracts.csv across all repos."""
    rows = []
    for repo in POOL_FILES:
        pcs = extract_private_contracts(repo)
        for p in pcs:
            rows.append({"repo": repo, "contract_id": p.contract_id,
                         "symbol": p.symbol, "file": p.file,
                         "line_start": p.line_range[0], "line_end": p.line_range[1],
                         "extraction_reason": p.extraction_reason,
                         "n_callers": len(p.callers),
                         "n_public_ancestors": len(p.public_ancestors)})
    with open(RESULTS / "private_contracts.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["repo","contract_id","symbol","file",
                            "line_start","line_end","extraction_reason",
                            "n_callers","n_public_ancestors"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return rows


def private_aware_select(repo: str, case_file: str, sym: str, kind: str,
                         claim: str, threshold=1.0, top1=True):
    """v4.2-A selector: candidate pool = v4.1 candidates UNION pool files that
    cover any PRIVATE_BEHAVIOR_CONTRACT of the changed file. Rank by assertion
    sensitivity; top1 pick (+ existing)."""
    comp = claim.replace("_completion", "")
    existing = list(EXISTING_VERIFY_SET[repo].get(comp, []))
    # public-gap candidates (v4.1)
    g = compute_gap(repo, claim, case_file, sym)
    pub_cands = candidates_for_gap(repo, g, existing)
    pub_ids = {o.obligation_id for o in pub_cands}
    # private-aware candidates: pool files covering a private symbol of the file
    priv = _private_contracts_for_file(repo, case_file)
    priv_cands = []
    if priv:
        for o in build_pool(repo):
            if o.obligation_id in pub_ids or o.target_tests in existing:
                continue
            covers_priv = _pool_covers_private_body(repo, o.target_tests, case_file, priv)
            if covers_priv:
                priv_cands.append((o, covers_priv))
    # merge + rank by assertion sensitivity (sym-level; for private use the
    # changed leaf too since public change often routes through private ones)
    all_cands = list(pub_cands) + [o for o, _ in priv_cands]
    # dedup
    seen = set(); uniq = []
    for o in all_cands:
        if o.obligation_id not in seen:
            seen.add(o.obligation_id); uniq.append(o)
    cand_scored = []
    for o in uniq:
        sens, _ = file_sensitivity(repo, o.target_tests, sym, kind)
        cand_scored.append((o, sens))
    cand_scored.sort(key=lambda x: -x[1])
    if top1:
        picked = [cand_scored[0][0]] if cand_scored else []
    else:
        picked = [o for o, _ in cand_scored]
    existing_set = set(existing)
    extra = [o.target_tests for o in picked if o.target_tests not in existing_set]
    files = list(dict.fromkeys(existing + extra))
    return files, picked


def run_v42a():
    cases = assemble_cases()
    n_break = sum(1 for c in cases if c["true_break"]) or 1
    det = 0; ncount = 0; cost = 0.0
    integ_cost_by_repo = {r: _cost(r, POOL_FILES[r]) for r in POOL_FILES}
    relcost = 0.0
    per_repo = {r: {"n": 0, "det": 0} for r in POOL_FILES}
    per_case = []
    early_stop_saved = 0
    for c in cases:
        comp = c["claim"].replace("_completion", "")
        existing = EXISTING_VERIFY_SET[c["repo"]].get(comp, [])
        existing_detects = any(_file_fails(c["case_id"], f) for f in existing)
        # Early stop
        if existing_detects:
            files = list(existing); early_stop_saved += 1
        else:
            files, _ = private_aware_select(c["repo"], c["file"], c["symbol"],
                                            c["kind"], c["claim"], top1=True)
        detect = _detects(c, files)
        if c["true_break"] and detect:
            det += 1
        ncount += len(files)
        cc = _cost(c["repo"], files)
        cost += cc
        relcost += cc / max(integ_cost_by_repo[c["repo"]], 0.001)
        per_repo[c["repo"]]["n"] += int(c["true_break"])
        if c["true_break"] and detect:
            per_repo[c["repo"]]["det"] += 1
        per_case.append({"case_id": c["case_id"], "repo": c["repo"], "claim": c["claim"],
                         "true_break": c["true_break"], "detect": int(detect),
                         "n_files": len(files), "early_stopped": int(existing_detects)})
    return {
        "strategy": "PrivateContract+AssertionTop1+EarlyStop",
        "detection_rate": round(det / n_break, 4),
        "vrr": round(det / n_break, 4),
        "avg_test_count": round(ncount / len(cases), 2),
        "avg_relative_cost": round(relcost / len(cases), 4),
        "per_repo": {k: round(v["det"]/max(v["n"],1),4) for k,v in per_repo.items()},
        "early_stop_hits": early_stop_saved,
        "n_cases": len(cases), "per_case": per_case,
    }


if __name__ == "__main__":
    dump_private_contracts()
    r = run_v42a()
    print(f"v4.2-A: det={r['detection_rate']} cost={r['avg_relative_cost']} "
          f"#tests={r['avg_test_count']} es={r['early_stop_hits']}")
    print("per-repo:", r["per_repo"])