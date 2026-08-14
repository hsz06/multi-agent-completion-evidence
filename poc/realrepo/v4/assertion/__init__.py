"""v4.1 AssertionSensitivity model + pytest-test AST analyzer.

Pure static analysis over PRISTINE test source. NEVER reads the held-out
per_file PASS/FAIL matrix. Analyzes each pytest test function for how
sensitive it is to a given change_kind on a given contract symbol.

Scoring is deterministic and fixed by spec rules — no held-out tuning.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent.parent))   # v3
sys.path.insert(0, str(_HERE.parent / "v3"))   # v3


@dataclass
class AssertionSensitivity:
    test_id: str
    contract_id: str
    change_kind: str
    direct_call_score: float = 0.0
    value_flow_score: float = 0.0
    assertion_score: float = 0.0
    exception_score: float = 0.0
    type_score: float = 0.0
    field_score: float = 0.0
    total_score: float = 0.0
    assertion_sensitivity: float = 0.0
    evidence: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


# map v3 mutation kinds -> change_kind categories the analyzer reasons about
KIND_TO_CATEGORY = {
    "CHANGE_RETURN_TYPE": "RETURN_TYPE_CHANGE",
    "ADD_OPTIONAL_PARAM": "COMPATIBLE",
    "ADD_REQUIRED_PARAM": "REQUIRED_PARAM_ADDED",
    "BODY_ONLY": "NON_SEMANTIC",
    "REMOVE_PARAM": "PARAM_REMOVED",
}


def _is_test_func(node):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
        and node.name.startswith("test_")


def _leaf_symbol(dotted: str) -> str:
    return dotted.split(".")[-1]


def _calls_in(func_node) -> list[ast.Call]:
    return [n for n in ast.walk(func_node) if isinstance(n, ast.Call)]


def _asserts_in(func_node) -> list:
    out = []
    for n in ast.walk(func_node):
        if isinstance(n, ast.Assert):
            out.append(n)
        # pytest.raises(...) used in `with`
        if isinstance(n, ast.With):
            for item in n.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Attribute) \
                        and ctx.func.attr == "raises":
                    out.append(n)
    return out


def _symbol_called(test_node, leaf: str) -> tuple[bool, list[ast.Call]]:
    hits = []
    for c in _calls_in(test_node):
        f = c.func
        name = None
        if isinstance(f, ast.Attribute):
            name = f.attr
        elif isinstance(f, ast.Name):
            name = f.id
        if name == leaf:
            hits.append(c)
    return bool(hits), hits


def _assigned_names(test_node, leaf: str):
    """Return set of local var names that receive the RETURN of a call to `leaf`,
    plus the call nodes. e.g. `eid = table.insert(...)` -> {'eid'}."""
    names = set()
    calls = []
    for n in ast.walk(test_node):
        if isinstance(n, ast.Assign):
            if isinstance(n.value, ast.Call):
                f = n.value.func
                fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if fname == leaf:
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
                    calls.append(n.value)
    return names, calls


def _var_used_in_assert(test_node, varnames: set) -> bool:
    """True if any assert expression references one of varnames."""
    for a in _asserts_in(test_node):
        for n in ast.walk(a):
            if isinstance(n, ast.Name) and n.id in varnames:
                return True
    return False


def _isinstance_or_type_assert(test_node, varnames: set) -> bool:
    for a in _asserts_in(test_node):
        if not isinstance(a, ast.Assert):
            continue
        call = a.test if isinstance(a.test, ast.Call) else None
        if call and isinstance(call.func, ast.Name) and call.func.id in ("isinstance", "type"):
            for arg in call.args:
                for n in ast.walk(arg):
                    if isinstance(n, ast.Name) and n.id in varnames:
                        return True
    return False


def _index_or_attr_in_assert(test_node, varnames: set) -> bool:
    """result['field'] or result.field or result.get('field') referenced in assert."""
    for a in _asserts_in(test_node):
        if not isinstance(a, ast.Assert):
            continue
        for n in ast.walk(a.test):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                    and n.value.id in varnames:
                return True
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                    and n.value.id in varnames:
                return True
    return False


def _raises_around_call(test_node, leaf: str) -> bool:
    """with pytest.raises(...) wrapping a call to `leaf`."""
    for n in ast.walk(test_node):
        if isinstance(n, ast.With):
            raises_ctx = any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "raises"
                for item in n.items
            )
            if raises_ctx:
                for b in n.body:
                    for c in _calls_in(b):
                        f = c.func
                        fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                        if fname == leaf:
                            return True
    return False


def _kwarg_referenced(test_node, leaf: str) -> bool:
    for c in _calls_in(test_node):
        f = c.func
        fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if fname == leaf and c.keywords:
            return True
    return False


def _field_accessed(test_node, varnames: set, field: str | None = None) -> bool:
    """Any subscript/attr access on a returned var (field-agnostic for now)."""
    for n in ast.walk(test_node):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                and n.value.id in varnames:
            return True
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                and n.value.id in varnames:
            return True
    return False


# ---------------------------------------------------------------------------
# Scoring (deterministic, spec-inspired, NOT tuned on held-out)
# ---------------------------------------------------------------------------

MAX_RAW = 10.0


def score_for_change(test_node, change_kind: str, leaf: str) -> AssertionSensitivity:
    cat = change_kind
    called, calls = _symbol_called(test_node, leaf)
    varnames, ret_calls = _assigned_names(test_node, leaf)
    asserted = _var_used_in_assert(test_node, varnames)
    # inline: call to `leaf` appears directly inside an assert expression
    inline_in_assert = False
    for a in _asserts_in(test_node):
        if not isinstance(a, ast.Assert):
            continue
        for n in ast.walk(a.test):
            if isinstance(n, ast.Call):
                f = n.func
                fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if fname == leaf:
                    inline_in_assert = True
    if inline_in_assert:
        asserted = True
    isinst = _isinstance_or_type_assert(test_node, varnames)
    idx = _index_or_attr_in_assert(test_node, varnames)
    field_acc = _field_accessed(test_node, varnames)
    raises = _raises_around_call(test_node, leaf)
    kw = _kwarg_referenced(test_node, leaf)

    evidence = {
        "direct_calls": [leaf] if called else [],
        "returned_vars": sorted(varnames),
        "return_flows_to_assert": asserted,
        "isinstance_assert": isinst,
        "field_index_in_assert": idx,
        "field_accessed": field_acc,
        "pytest_raises_around_call": raises,
        "kwarg_referenced": kw,
    }

    direct = 0.0
    flow = 0.0
    assertion = 0.0
    exception = 0.0
    type_s = 0.0
    fsc = 0.0

    direct = 1.0 if called else 0.0

    if cat in ("RETURN_TYPE_CHANGE", "RETURN_VALUE_SHAPE_CHANGE",
               "RETURN_CONTRACT"):
        if asserted:
            assertion += 3.0
        if isinst:
            type_s += 3.0
        if asserted or isinst:
            flow += 2.0
        if idx:
            fsc += 2.0
        if field_acc and not (asserted or isinst):
            fsc += 1.0
        if varnames and not (asserted or isinst or idx or field_acc):
            flow += 1.0   # consumed but not asserted
    elif cat in ("REQUIRED_PARAM_ADDED", "PARAM_REMOVED", "PARAM_RENAMED",
                 "PARAM_TYPE_CHANGE", "EXCEPTION_BEHAVIOR_CHANGE"):
        if raises:
            exception += 3.0
        if called and (not calls[0].keywords == [] if False else True):
            pass
        # direct call to the changed signature with positional/keyword form
        if called:
            assertion += 2.0
        if kw:
            assertion += 2.0
        if calls:
            # positional arity usage is the signal; assign base
            flow += 1.0
    elif cat in ("SCHEMA_FIELD_REMOVED", "SCHEMA_FIELD_RENAMED",
                 "SCHEMA_FIELD_TYPE_CHANGE"):
        if idx:
            fsc += 3.0
        if field_acc:
            fsc += 2.0
        if asserted:
            assertion += 2.0
    else:
        # COMPATIBLE / NON_SEMANTIC / UNKNOWN — return-flow default
        if asserted:
            assertion += 2.0
        if called:
            direct = 1.0

    total = direct + flow + assertion + exception + type_s + fsc
    total = min(total, MAX_RAW)
    sens = round(total / MAX_RAW, 4)

    return AssertionSensitivity(
        test_id="", contract_id="", change_kind=cat,
        direct_call_score=direct, value_flow_score=flow,
        assertion_score=assertion, exception_score=exception,
        type_score=type_s, field_score=fsc,
        total_score=round(total, 4),
        assertion_sensitivity=sens,
        evidence=evidence,
    )


def analyze_test_file(repo: str, testfile: str) -> dict:
    """Return {test_function_name: AssertionSensitivity-neutral} per test fn in
    the file, scored against a GIVEN (contract, change_kind) later. Here we
    parse + return the ast FunctionDef nodes + leaf-callable detection, to be
    re-scored per change cheaply."""
    from engine import REPOS_DIR
    p = REPOS_DIR / repo / testfile
    src = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if _is_test_func(node):
            # which contract leaves does this test call?
            leaves = set()
            for c in _calls_in(node):
                f = c.func
                nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if nm:
                    leaves.add(nm)
            out[node.name] = {"node_lineno": node.lineno, "called_leaves": sorted(leaves)}
    return out


# ---------------------------------------------------------------------------
# Per (test_file, contract_symbol, change_kind) sensitivity
# ---------------------------------------------------------------------------

_TEST_FILE_CACHE: dict = {}


def _parse_test_file(repo: str, testfile: str):
    key = (repo, testfile)
    if key in _TEST_FILE_CACHE:
        return _TEST_FILE_CACHE[key]
    from engine import REPOS_DIR
    p = REPOS_DIR / repo / testfile
    src = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    _TEST_FILE_CACHE[key] = tree
    return tree


def file_sensitivity(repo: str, testfile: str, contract_symbol: str,
                     change_kind: str) -> tuple[float, dict]:
    """Aggregate sensitivity of all test fns in `testfile` w.r.t. the changed
    symbol+kind. Returns (max_sensitivity, best_evidence). We use the MAX over
    test fns (file-level granularity) — the file is 'sensitive' if its most
    sensitive test fn is."""
    tree = _parse_test_file(repo, testfile)
    if tree is None:
        return 0.0, {}
    leaf = _leaf_symbol(contract_symbol)
    cat = KIND_TO_CATEGORY.get(change_kind, change_kind)
    best = 0.0
    best_ev = {}
    for node in tree.body:
        if _is_test_func(node):
            sens = score_for_change(node, cat, leaf)
            if sens.assertion_sensitivity > best:
                best = sens.assertion_sensitivity
                best_ev = {"test_fn": node.name, **sens.evidence,
                           "total_score": sens.total_score}
    return best, best_ev


if __name__ == "__main__":
    # smoke: how sensitive is test_tables.py to Table.insert/RETURN_TYPE_CHANGE?
    s, ev = file_sensitivity("tinydb", "tests/test_tables.py",
                             "Table.insert", "CHANGE_RETURN_TYPE")
    print("test_tables vs Table.insert return-type:", s, ev)
    s2, ev2 = file_sensitivity("tinydb", "tests/test_utils.py",
                               "Table.insert", "CHANGE_RETURN_TYPE")
    print("test_utils vs Table.insert return-type:", s2, ev2)