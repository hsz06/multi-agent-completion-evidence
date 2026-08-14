"""Unit tests for v3 core: contract model, extractor, change classifier, mutation engine.

Deterministic, no LLM, no network. Run from poc/realrepo:
    python3 -m pytest v3/tests/test_v3.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # realrepo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # v3

import pytest
from contracts.model import ContractNode, ContractType, ChangeType, Granularity, DependencyEdge, Provenance
from contracts.extractor import ContractExtractor, REPOS_DIR
from contracts.change_classifier import ContractChangeClassifier
from contracts.mutations import mutate, change_type_for as change_type_for_kind
import ast


# --- extractor --------------------------------------------------------------

def test_extractor_finds_tinydb_contracts():
    ext = ContractExtractor(REPOS_DIR / "tinydb", "tinydb")
    nodes = ext.extract()
    types = {n.contract_type for n in nodes}
    assert ContractType.FUNCTION_SIGNATURE in types
    assert ContractType.PUBLIC_SYMBOL in types
    # Table.insert signature present
    sigs = {n.symbol for n in nodes if n.contract_type == ContractType.FUNCTION_SIGNATURE}
    assert "Table.insert" in sigs
    assert "Table.all" in sigs


def test_extractor_contract_id_is_stable():
    ext = ContractExtractor(REPOS_DIR / "toolz", "toolz")
    n = next(x for x in ext.extract()
             if x.contract_type == ContractType.FUNCTION_SIGNATURE and x.symbol == "groupby")
    assert n.contract_id == "toolz/itertoolz.py::FUNCTION_SIGNATURE::groupby"
    assert n.artifact_id == "toolz/itertoolz.py"


# --- change classifier ------------------------------------------------------

def _sigs_from(src):
    ext = ContractExtractor(REPOS_DIR / "tinydb", "tinydb")
    return ext.extract_file(Path("dummy.py")) if False else _extract(src)


def _extract(src):
    """Extract ContractNodes from a source string via a temp-file-free path."""
    import tempfile
    # reuse extractor on a temp dir
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.py"
        p.write_text(src)
        ext = ContractExtractor(Path(td), "m")
        return ext.extract_file(p)


def test_classifier_function_no_change():
    clf = ContractChangeClassifier()
    src = "def f(a, b):\n    return a + b\n"
    old = _extract(src); new = _extract(src)
    assert clf.classify(old, new, "m.py") == ChangeType.NON_SEMANTIC


def test_classifier_added_required_param_breaking():
    clf = ContractChangeClassifier()
    old = _extract("def insert(doc):\n    return doc\n")
    new = _extract("def insert(doc, *, tag):\n    return doc\n")
    assert clf.classify(old, new, "m.py") == ChangeType.BREAKING


def test_classifier_return_annotation_change_potentially_breaking():
    clf = ContractChangeClassifier()
    old = _extract("def all(self) -> list:\n    return []\n")
    new = _extract("def all(self) -> dict:\n    return {}\n")
    # return-annotation change -> POTENTIALLY_BREAKING
    assert clf.classify(old, new, "m.py") == ChangeType.POTENTIALLY_BREAKING


def test_classifier_misses_untyped_return_value_change():
    """Honest limitation: without a return annotation, a return-VALUE change
    is invisible to the contract classifier (signature identical) -> NON_SEMANTIC.
    Experiments therefore label change-type from the mutation KIND, not the classifier."""
    clf = ContractChangeClassifier()
    old = _extract("def all(self):\n    return list(iter(self))\n")
    new = _extract("def all(self):\n    return {'_ret': list(iter(self))}\n")
    assert clf.classify(old, new, "m.py") == ChangeType.NON_SEMANTIC


def test_classifier_removed_symbol_breaking():
    clf = ContractChangeClassifier()
    old = _extract("def a():\n    pass\ndef b():\n    pass\n")
    new = _extract("def a():\n    pass\n")
    assert clf.classify(old, new, "m.py") == ChangeType.BREAKING


# --- mutation engine --------------------------------------------------------

def test_mutate_body_only_safe_and_parses():
    src = "def is_iterable(obj):\n    return hasattr(obj, '__iter__')\n"
    new = mutate(src, "is_iterable", "BODY_ONLY")
    assert new != src
    ast.parse(new)   # must remain valid Python


def test_mutate_change_return_type():
    src = "def all(self):\n    return list(iter(self))\n"
    new = mutate(src, "all", "CHANGE_RETURN_TYPE")
    ast.parse(new)
    assert "{'_ret':" in new


def test_mutate_add_required_param_on_method():
    src = "class Table:\n    def insert(self, doc) -> int:\n        return 1\n"
    new = mutate(src, "Table.insert", "ADD_REQUIRED_PARAM")
    ast.parse(new)
    assert "req_marker_" in new


def test_mutate_kind_to_change_type():
    from contracts.mutations import change_type_for
    assert change_type_for("BODY_ONLY") == ChangeType.NON_SEMANTIC
    assert change_type_for("ADD_REQUIRED_PARAM") == ChangeType.BREAKING
    assert change_type_for("CHANGE_RETURN_TYPE") == ChangeType.POTENTIALLY_BREAKING
    assert change_type_for("ADD_OPTIONAL_PARAM") == ChangeType.COMPATIBLE


# --- model invariants -------------------------------------------------------

def test_completionclaim_v3_has_no_dependency_answer():
    from contracts.model import CompletionClaim
    fields = CompletionClaim.__dataclass_fields__
    assert "based_on_artifact_versions" not in fields
    assert "verification_obligations" in fields
    assert "produced_artifacts" in fields


def test_dependencyedge_contract_granularity_roundtrip():
    e = DependencyEdge(source="f.py::FUNCTION_SIGNATURE::Foo.bar",
                      target="testing_completion",
                      relation_type="CONTRACT->COMPLETION",
                      scope=frozenset({ChangeType.BREAKING}),
                      granularity=Granularity.CONTRACT, provenance=Provenance.MANUAL)
    d = e.to_dict()
    assert d["granularity"] == "CONTRACT"
    e2 = DependencyEdge.from_dict(d)
    assert e2.granularity == Granularity.CONTRACT
    assert ChangeType.BREAKING in e2.scope


# --- two-track separation invariant ----------------------------------------

def test_simulated_agent_label():
    from trajectories.simulated_agent import simulate_trajectory
    from engine import build_change_registry
    c = build_change_registry()[0]
    t = simulate_trajectory(c, "LOCAL")
    assert t.get("trajectory_source") == "simulated_agent"


def test_real_llm_pilot_label():
    from trajectories.real_agent import PILOT_TRAJECTORIES
    for tr in PILOT_TRAJECTORIES:
        assert tr["trajectory_source"] == "real_llm_agent"
        assert tr["model"] == "claude-coding-subagent"