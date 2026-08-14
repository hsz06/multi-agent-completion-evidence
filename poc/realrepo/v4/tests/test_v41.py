"""Unit tests for v4.1 assertion analyzer + early-stop. Deterministic, no LLM.
Run: python3 -m pytest v4/tests/test_v41.py -q"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # realrepo
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "v3"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # v4


# --- assertion analyzer -----------------------------------------------------

def test_returns_true_for_tinydb_sens_file():
    """test_tinydb asserts insert return value -> sensitive to RETURN_TYPE_CHANGE."""
    from assertion import file_sensitivity
    s, ev = file_sensitivity("tinydb", "tests/test_tinydb.py",
                             "Table.insert", "CHANGE_RETURN_TYPE")
    assert s > 0
    assert ev.get("return_flows_to_assert") is True or ev.get("direct_calls")


def test_zero_for_unrelated_test():
    from assertion import file_sensitivity
    s, _ = file_sensitivity("tinydb", "tests/test_utils.py",
                            "Table.insert", "CHANGE_RETURN_TYPE")
    assert s == 0.0   # test_utils doesn't call insert


def test_inlined_assert_return_detected():
    """assert db.insert(...) == 1 (return value asserted inline) is high-sens."""
    from assertion import score_for_change
    src = "def test_x(db):\n    assert db.insert({'a':1}) == 1\n"
    tree = ast.parse(src)
    fn = tree.body[0]
    r = score_for_change(fn, "RETURN_TYPE_CHANGE", "insert")
    assert r.assertion_score >= 3.0
    assert r.assertion_sensitivity > 0.3


def test_isinstance_assert_scored():
    from assertion import score_for_change
    src = "def test_x(t):\n    r = t.all()\n    assert isinstance(r, list)\n"
    fn = ast.parse(src).body[0]
    r = score_for_change(fn, "RETURN_TYPE_CHANGE", "all")
    assert r.type_score > 0
    assert r.evidence.get("isinstance_assert") is True


def test_pytest_raises_scored_for_required_param():
    from assertion import score_for_change
    src = ("import pytest\n"
           "def test_x(t):\n"
           "    with pytest.raises(TypeError):\n"
           "        t.insert()\n")
    fn = ast.parse(src).body[1]
    r = score_for_change(fn, "REQUIRED_PARAM_ADDED", "insert")
    assert r.exception_score > 0
    assert r.evidence.get("pytest_raises_around_call") is True


def test_change_kind_mapping():
    from assertion import KIND_TO_CATEGORY
    assert KIND_TO_CATEGORY["CHANGE_RETURN_TYPE"] == "RETURN_TYPE_CHANGE"
    assert KIND_TO_CATEGORY["ADD_REQUIRED_PARAM"] == "REQUIRED_PARAM_ADDED"


# --- selector ---------------------------------------------------------------

def test_assertion_aware_includes_existing():
    from assertion_selector import assertion_aware_select
    from obligation.coverage import compute_gap
    from config import EXISTING_VERIFY_SET
    g = compute_gap("tinydb", "dev_b_completion", "tinydb/table.py", "Table.insert")
    ex = EXISTING_VERIFY_SET["tinydb"]["dev_b"]
    sel = assertion_aware_select("tinydb", g, ex, "Table.insert",
                                 "CHANGE_RETURN_TYPE", threshold=0.8)
    # selected is extra only; existing comes from caller
    for f in sel.selected_files:
        assert f not in ex


def test_coverage_only_reproduces_v4():
    """CoverageOnly@1.0 must reproduce v4's coverage-only selection."""
    from assertion_selector import coverage_only_select
    from obligation.coverage import compute_gap
    from config import EXISTING_VERIFY_SET
    g = compute_gap("tinydb", "testing_completion", "tinydb/table.py", "Table.all")
    sel = coverage_only_select("tinydb", g,
                               EXISTING_VERIFY_SET["tinydb"]["testing"], threshold=1.0)
    assert sel.coverage_achieved >= 0.5


# --- anti-leakage -----------------------------------------------------------

def test_assertion_sensitivity_no_oracle_dependency():
    """The analyzer module must not import or read the held-out oracle file."""
    import assertion
    src = Path(assertion.__file__).read_text()
    assert "per_file.json" not in src, "analyzer must not read held-out per_file.json"
    assert "evaluation_private_oracle" not in src, "analyzer must not reference oracle dir"
    # and must not import strategies/ORACLE (which holds the matrix)
    assert "from strategies import" not in src
    assert "import strategies" not in src


def test_early_stop_never_uses_oracle_for_ranking():
    """The selector module must not read the held-out per_file matrix."""
    import assertion_selector
    s2 = Path(assertion_selector.__file__).read_text()
    assert "per_file.json" not in s2, "selector must not read held-out per_file.json"
    assert "evaluation_private_oracle" not in s2


# --- regression: same 56 samples -------------------------------------------

def test_same_56_samples_as_v4():
    from strategies import assemble_cases
    cases = assemble_cases()
    assert len(cases) == 56, f"v4.1 must run on exactly 56 (case,claim) samples, got {len(cases)}"