"""Assert-Aware selector + Early-Stop pipeline for v4.1.

Reuses v4's coverage gap + pool; adds assertion sensitivity (from pristine
test-source AST) to rank candidate obligations, and an early-stop gate that
runs the existing verify-set FIRST and stops if it already FAILs.

Anti-leakage: sensitivity comes only from pristine AST; the held-out per_file
PASS/FAIL matrix is never read by the selector. Early-stop decides "should I
add obligations?" based on the REAL current run of the existing verify-set on
the MUTATED tree — but that real run is what we are scoring detection against,
so it cannot be a leaked signal (it IS the verification we are measuring).
We compute early-stop outcomes from the held-out matrix WITHOUT feeding its
per-file PASS/FAIL into ranking (early-stop only inspects the existing
verify-set's own files, which is allowed: rerunning your own verify-set is the
verification action under measurement).
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent / "v3"))   # v3
sys.path.insert(0, str(_HERE))                 # v4

from obligation.pool import build_pool
from obligation.coverage import (required_contracts, covered_by_files,
                                  candidates_for_gap, greedy_select)
from obligation.model import CoverageGap, SelectionResult
from config import EXISTING_VERIFY_SET, POOL_FILES
from assertion import (file_sensitivity, KIND_TO_CATEGORY)
from engine import REPOS_CFG, build_change_registry
from contracts.mutations import change_type_for

DEFAULT_ALPHA, DEFAULT_BETA = 0.4, 0.6


def _change_kind_for_case(kind: str) -> str:
    return KIND_TO_CATEGORY.get(kind, kind)


def assertion_aware_select(repo: str, gap: CoverageGap, existing_files: list[str],
                           changed_symbol: str, change_kind: str,
                           threshold: float = 0.8,
                           alpha: float = DEFAULT_ALPHA,
                           beta: float = DEFAULT_BETA) -> SelectionResult:
    """Coverage hard-constraint + assertion-aware greedy ranking.

    Candidate pool = coverage-positive UNION direct-call-positive (a test that
    statically calls the changed leaf but may not register line coverage, e.g.
    via a wrapper). Then rank by (alpha*gain + beta*sens)/cost (or minus gamma
    cost; we use the benefit/cost form).
    """
    req = set(gap.required_contracts)
    existing_cov = set(covered_by_files(repo, existing_files))
    selected = []
    selected_cov = set(existing_cov)
    leaf = changed_symbol.split(".")[-1]
    cat = _change_kind_for_case(change_kind)

    # candidate set: coverage-positive OR direct-call-positive (static)
    cov_candidates = candidates_for_gap(repo, gap, existing_files)
    cov_ids = {o.obligation_id for o in cov_candidates}
    direct_candidates = []
    for o in build_pool(repo):
        if o.target_tests in existing_files or o.obligation_id in cov_ids:
            continue
        # static: test file source references the changed leaf callably
        sens, ev = file_sensitivity(repo, o.target_tests, changed_symbol, change_kind)
        if sens > 0:   # leaf is called somewhere in this test file
            direct_candidates.append(o)
    candidates = list(cov_candidates) + direct_candidates
    # de-dup
    seen = set()
    uniq = []
    for o in candidates:
        if o.obligation_id not in seen:
            seen.add(o.obligation_id)
            uniq.append(o)
    candidates = uniq

    remaining = list(candidates)
    remaining = [o for o in remaining
                if len((set(o.covered_contracts) & req) - selected_cov) > 0]
    while remaining:
        achieved = len(selected_cov & req) / max(1, len(req))
        if achieved >= threshold:
            break
        best, best_score = None, -1e9
        for o in remaining:
            gain = len((set(o.covered_contracts) & req) - selected_cov)
            sens, _ = file_sensitivity(repo, o.target_tests, changed_symbol, change_kind)
            cost = max(o.estimated_cost, 0.01)
            # coverage gain is the hard enrollment reason (gain>0 enforced
            # above); assertion sensitivity ranks WHICH covering test to pick.
            score = (alpha * gain + beta * sens) / cost
            if score > best_score:
                best, best_score = o, score
        if best is None:
            break
        selected.append(best)
        selected_cov |= (set(best.covered_contracts) & req)
        remaining = [o for o in remaining
                     if o is not best
                     and len((set(o.covered_contracts) & req) - selected_cov) > 0]
    achieved = len(selected_cov & req) / max(1, len(req))
    cost = sum(o.estimated_cost for o in selected)
    return SelectionResult(
        strategy="AssertionAware",
        selected=[o.obligation_id for o in selected],
        selected_files=[o.target_tests for o in selected],
        covered_missing=sorted((selected_cov & req) - existing_cov),
        threshold=threshold,
        coverage_achieved=round(achieved, 4),
        estimated_cost=round(cost, 4),
    )


def coverage_only_select(repo: str, gap: CoverageGap, existing_files: list[str],
                         threshold: float = 0.8) -> SelectionResult:
    """v4 CoverageOnly selector (identical to v4 greedy_select)."""
    return greedy_select(repo, gap, existing_files, threshold=threshold)


if __name__ == "__main__":
    from obligation.coverage import compute_gap
    g = compute_gap("tinydb", "dev_b_completion", "tinydb/table.py", "Table.insert")
    ex = EXISTING_VERIFY_SET["tinydb"]["dev_b"]
    oa = assertion_aware_select("tinydb", g, ex, "Table.insert", "CHANGE_RETURN_TYPE",
                                threshold=1.0)
    print("AssertionAware dev_b/Table.insert:", oa.selected_files,
          "cov=", oa.coverage_achieved, "cost=", oa.estimated_cost)
    co = coverage_only_select("tinydb", g, ex, threshold=1.0)
    print("CoverageOnly  dev_b/Table.insert:", co.selected_files,
          "cov=", co.coverage_achieved, "cost=", co.estimated_cost)