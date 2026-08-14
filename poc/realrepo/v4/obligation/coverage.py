"""Coverage Gap computation + greedy set-cover selector.

Anti-leakage: required_contracts and coverage are derived ONLY from
- the changed file's static contracts (AST, pristine)
- pool obligations' pre-change coverage (pristine)
NEVER from the held-out per_file PASS/FAIL matrix.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent.parent / "v3"))   # v3
sys.path.insert(0, str(_HERE.parent))                 # v4

from contracts.extractor import ContractExtractor
from contracts.model import ContractType
from engine import REPOS_DIR, REPOS_CFG
from config import EXISTING_VERIFY_SET
from obligation.pool import build_pool
from obligation.model import CoverageGap, SelectionResult, VerificationObligation

_CONTRACTS_CACHE = {}


def _contracts(repo: str) -> dict:
    if repo not in _CONTRACTS_CACHE:
        ext = ContractExtractor(REPOS_DIR / repo, repo)
        _CONTRACTS_CACHE[repo] = {c.contract_id: c for c in ext.extract()}
    return _CONTRACTS_CACHE[repo]


def required_contracts(repo: str, changed_file: str, changed_symbol: str) -> list:
    """Contracts the affected completion SHOULD re-verify given the change:
    all public FUNCTION_SIGNATURE contracts defined in the changed file.
    Deterministic, AST-only, independent of any test outcome."""
    out = []
    for cid, c in _contracts(repo).items():
        if c.artifact_id == changed_file and c.contract_type == ContractType.FUNCTION_SIGNATURE:
            out.append(cid)
    # always include the explicitly changed contract even if private-filtered
    explicit = f"{changed_file}::FUNCTION_SIGNATURE::{changed_symbol}"
    if explicit not in out:
        out.append(explicit)
    return out


def covered_by_files(repo: str, files: list[str]) -> list:
    """Contracts covered (pre-change) by the given test files, via the pool."""
    pool = {o.target_tests: o for o in build_pool(repo)}
    cov = set()
    for f in files:
        o = pool.get(f)
        if o:
            cov.update(o.covered_contracts)
    return sorted(cov)


def compute_gap(repo: str, claim: str, changed_file: str, changed_symbol: str) -> CoverageGap:
    comp_short = claim.replace("_completion", "")   # dev_b / testing / dev_a
    existing = EXISTING_VERIFY_SET[repo].get(comp_short, [])
    req = required_contracts(repo, changed_file, changed_symbol)
    cur = covered_by_files(repo, existing)
    cur_set = set(cur)
    missing = [c for c in req if c not in cur_set]
    return CoverageGap(
        claim=claim, repo=repo,
        changed_contract=f"{changed_file}::FUNCTION_SIGNATURE::{changed_symbol}",
        required_contracts=req, currently_covered=cur,
        missing_coverage=missing, gap=bool(missing),
    )


# ---------------------------------------------------------------------------
# Greedy set-cover selector
# ---------------------------------------------------------------------------

def candidates_for_gap(repo: str, gap: CoverageGap,
                       existing_files: list[str]) -> list[VerificationObligation]:
    """Pool obligations (excluding existing verify-set files) that cover any
    missing contract — the selectable candidate set."""
    pool = build_pool(repo)
    existing_set = set(existing_files)
    missing = set(gap.missing_coverage)
    out = []
    for o in pool:
        if o.target_tests in existing_set:
            continue
        if missing & set(o.covered_contracts):
            out.append(o)
    return out


def greedy_select(repo: str, gap: CoverageGap, existing_files: list[str],
                  threshold: float = 0.8) -> SelectionResult:
    """Greedymax coverage-gain/cost until `threshold` of required_contracts is
    covered by existing+selected. Returns selected obligations + cost."""
    req = set(gap.required_contracts)
    existing_cov = set(covered_by_files(repo, existing_files))
    selected = []
    selected_cov = set(existing_cov)
    candidates = candidates_for_gap(repo, gap, existing_files)
    # greedy
    remaining = list(candidates)
    while remaining:
        achieved = len(selected_cov & req) / max(1, len(req))
        if achieved >= threshold:
            break
        # pick best gain/cost
        best, best_ratio = None, -1.0
        for o in remaining:
            gain = len((set(o.covered_contracts) & req) - selected_cov)
            if gain == 0:
                continue
            cost = max(o.estimated_cost, 0.01)
            ratio = gain / cost
            if ratio > best_ratio or (ratio == best_ratio and
                                      o.estimated_cost < (best.estimated_cost if best else 1e9)):
                best, best_ratio = o, ratio
        if best is None:
            break
        selected.append(best)
        selected_cov |= (set(best.covered_contracts) & req)
        remaining = [o for o in remaining if o is not best]
    achieved = len(selected_cov & req) / max(1, len(req))
    cost = sum(o.estimated_cost for o in selected)
    return SelectionResult(
        strategy="ObligationAware",
        selected=[o.obligation_id for o in selected],
        selected_files=[o.target_tests for o in selected],
        covered_missing=sorted((selected_cov & req) - existing_cov),
        threshold=threshold,
        coverage_achieved=round(achieved, 4),
        estimated_cost=round(cost, 4),
    )


if __name__ == "__main__":
    # smoke on a tinydb case
    g = compute_gap("tinydb", "testing_completion", "tinydb/table.py", "Table.insert")
    print("gap:", g.to_dict())
    res = greedy_select("tinydb", g, EXISTING_VERIFY_SET["tinydb"]["testing"], 0.8)
    print("selection:", res.to_dict())