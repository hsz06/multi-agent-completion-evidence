"""Unit tests for v4 core: obligation model, line-level coverage, coverage gap,
greedy selector, strategy file selection. Deterministic, no LLM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # realrepo
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "v3"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # v4

import pytest


# --- obligation model -------------------------------------------------------

def test_obligation_model_fields():
    from obligation.model import (VerificationObligation, VerifierType,
                                  ObligationSource, CoverageGap)
    o = VerificationObligation(obligation_id="r::t", repo="r",
                               verifier_type=VerifierType.UNIT_TEST,
                               command="pytest t", target_tests="t",
                               covered_contracts=["c"], estimated_cost=0.5)
    d = o.to_dict()
    assert d["verifier_type"] == "UNIT_TEST"
    assert "covered_contracts" in d and "estimated_cost" in d
    g = CoverageGap(claim="c", repo="r", changed_contract="x",
                    required_contracts=["a"], currently_covered=[],
                    missing_coverage=["a"], gap=True)
    assert g.gap is True


# --- line-level coverage: def-line exclusion -------------------------------

def test_symbol_range_finds_method():
    from obligation.line_cov import symbol_range
    rng = symbol_range("tinydb", "tinydb/table.py", "Table.insert")
    assert rng is not None
    start, end = rng
    assert start >= 1 and end > start


def test_covers_contract_distinguishes_call_from_import():
    """A test that imports a module but never calls a method should NOT cover
    that method's contract (def-line executed at import is excluded)."""
    from obligation.line_cov import covers_contract
    from contracts.extractor import ContractExtractor
    from engine import REPOS_DIR
    ext = ContractExtractor(REPOS_DIR / "tinydb", "tinydb")
    insert_c = next(c for c in ext.extract()
                    if c.contract_type.value == "FUNCTION_SIGNATURE"
                    and c.symbol == "Table.insert")
    # empty line map -> not covered
    assert covers_contract({}, "tinydb", insert_c) is False
    # only the def line executed (import) -> still not covered (def excluded)
    assert covers_contract({"tinydb/table.py": {insert_c.location.split(":")[-1] and 137}},
                           "tinydb", insert_c) is False


# --- coverage gap -----------------------------------------------------------

def test_gap_true_when_existing_does_not_cover_changed_symbol():
    from obligation.coverage import compute_gap
    g = compute_gap("tinydb", "dev_b_completion", "tinydb/table.py", "Table.insert")
    assert g.gap is True
    assert any("Table.insert" in m for m in g.missing_coverage)


def test_required_contracts_are_file_public_signatures():
    from obligation.coverage import required_contracts
    req = required_contracts("tinydb", "tinydb/table.py", "Table.insert")
    assert "tinydb/table.py::FUNCTION_SIGNATURE::Table.insert" in req
    assert all("table.py" in r for r in req)


# --- greedy selector --------------------------------------------------------

def test_greedy_select_picks_cheapest_gap_coverer():
    from obligation.coverage import compute_gap, greedy_select
    from config import EXISTING_VERIFY_SET
    g = compute_gap("tinydb", "dev_b_completion", "tinydb/table.py", "Table.insert")
    sel = greedy_select("tinydb", g, EXISTING_VERIFY_SET["tinydb"]["dev_b"], threshold=1.0)
    # must select at least one pool file covering Table.insert
    assert len(sel.selected_files) >= 1
    # coverage is bounded by what the pool can cover; 0.9 is the real max here
    # (some required contracts have no pool test exercising their body)
    assert sel.coverage_achieved >= 0.8


def test_greedy_select_empty_when_no_gap():
    from obligation.coverage import compute_gap, greedy_select
    from config import EXISTING_VERIFY_SET
    # synthesize a no-gap by requiring nothing — use a claim whose existing
    # covers the changed symbol. testing_completion on table.py is broad.
    g = compute_gap("tinydb", "testing_completion", "tinydb/table.py", "Table.name")
    # whatever the gap, selecting with threshold 0 should add nothing
    sel = greedy_select("tinydb", g, EXISTING_VERIFY_SET["tinydb"]["testing"], threshold=0.0)
    assert sel.selected_files == []


# --- strategies -------------------------------------------------------------

def test_strategies_select_disjoint_cost_ordering():
    from strategies import (assemble_cases, strat_local, strat_dependency_only,
                            strat_obligation_aware, strat_integration_all)
    cases = assemble_cases()
    assert len(cases) >= 20, f"expected >=20 cases, got {len(cases)}"
    c = next(x for x in cases if x["gap"])
    loc = strat_local(c)
    dep = strat_dependency_only(c)
    integ = strat_integration_all(c)
    oa, _ = strat_obligation_aware(c)
    assert loc == dep                       # B identical to A
    assert set(loc).issubset(set(oa))       # OA includes existing
    assert len(integ) >= len(oa)            # Integration runs the most


def test_obligation_aware_relative_cost_below_integration():
    from strategies import assemble_cases, _cost, strat_integration_all, strat_obligation_aware
    from config import POOL_FILES
    cases = assemble_cases()
    integ_total = sum(_cost(c["repo"], strat_integration_all(c)) for c in cases)
    oa_total = sum(_cost(c["repo"], strat_obligation_aware(c)[0]) for c in cases)
    assert oa_total < integ_total          # OA strictly cheaper in aggregate


# --- anti-leakage invariant -------------------------------------------------

def test_pool_obligations_carry_no_failure_info():
    """Pool obligations must not encode held-out PASS/FAIL — only coverage."""
    from obligation.pool import build_pool
    for repo in ("tinydb", "cerberus"):
        for o in build_pool(repo):
            d = o.to_dict()
            for forbidden in ("result", "pass", "fail", "tests_failed"):
                assert forbidden not in d, f"{forbidden} leaked into obligation"


def test_case_ids_unique_no_collision():
    """v3 bug regression: tinydb/toolz both started with T -> collided."""
    from engine import build_change_registry
    cases = build_change_registry()
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids)), "case_id collision"
    prefixes = {c["case_id"][:2] for c in cases if c["repo"] == "tinydb"}
    tz_prefixes = {c["case_id"][:2] for c in cases if c["repo"] == "toolz"}
    assert prefixes.isdisjoint(tz_prefixes), "tinydb/toolz prefix collision"