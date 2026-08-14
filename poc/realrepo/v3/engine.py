"""v3 engine: repos, change registry, calibration, G*, dependency instances,
contract- and file-level invalidation, completion gate.

Calibration runs REAL pytest on focused verify-set subsets (deterministic,
cached on disk). Ground truth per (change, completion, regime) = the verify-set's
real PASS/FAIL under the change.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
RR = _HERE.parent                     # poc/realrepo
sys.path.insert(0, str(RR))           # import v2 common
sys.path.insert(0, str(_HERE))

from common.repo_driver import RepoDriver
from contracts.extractor import ContractExtractor, REPOS_DIR
from contracts.model import (ContractNode, ContractType, ChangeType, ClaimStatus,
                             Granularity, Provenance, DependencyEdge, Candidate,
                             CompletionClaim, VerificationObligation)
from contracts.mutations import mutate

# ---------------------------------------------------------------------------
# Repo configuration: verify-set regimes per completion + oracle
# ---------------------------------------------------------------------------
# Each completion (dev_a = producer-module owner, dev_b = consumer-module owner,
# testing = integration) has a verify-set at three breadths. Oracle = the
# broadest realistic suite. Sets are focused files to bound runtime (esp.
# pyparsing, whose full suite is 26s).

REPOS_CFG = {
    "tinydb": {
        "pkg": "tinydb", "test_ignore": [],
        "verify": {
            "LOCAL":      {"dev_a": ["tests/test_tables.py"],
                           "dev_b": ["tests/test_utils.py"],
                           "testing": ["tests/test_operations.py"]},
            "MODULE":     {"dev_a": ["tests/test_tables.py", "tests/test_tinydb.py"],
                           "dev_b": ["tests/test_utils.py", "tests/test_queries.py"],
                           "testing": ["tests/test_operations.py", "tests/test_tables.py"]},
            "INTEGRATION":{"dev_a": ["tests/test_tables.py", "tests/test_tinydb.py", "tests/test_storages.py"],
                           "dev_b": ["tests/test_utils.py", "tests/test_queries.py", "tests/test_middlewares.py"],
                           "testing": ["tests/test_operations.py", "tests/test_tables.py", "tests/test_tinydb.py"]},
        },
        "oracle": ["tests/test_tinydb.py", "tests/test_tables.py",
                   "tests/test_operations.py", "tests/test_queries.py"],
    },
    "cerberus": {
        "pkg": "cerberus", "test_ignore": [],
        "verify": {
            "LOCAL":      {"dev_a": ["cerberus/tests/test_utils.py"],
                           "dev_b": ["cerberus/tests/test_errors.py"],
                           "testing": ["cerberus/tests/test_normalization.py"]},
            "MODULE":     {"dev_a": ["cerberus/tests/test_utils.py", "cerberus/tests/test_schema.py"],
                           "dev_b": ["cerberus/tests/test_errors.py", "cerberus/tests/test_registries.py"],
                           "testing": ["cerberus/tests/test_normalization.py", "cerberus/tests/test_customization.py"]},
            "INTEGRATION":{"dev_a": ["cerberus/tests/test_utils.py", "cerberus/tests/test_schema.py", "cerberus/tests/test_validation.py"],
                           "dev_b": ["cerberus/tests/test_errors.py", "cerberus/tests/test_registries.py", "cerberus/tests/test_assorted.py"],
                           "testing": ["cerberus/tests/test_normalization.py", "cerberus/tests/test_customization.py", "cerberus/tests/test_validation.py"]},
        },
        "oracle": ["cerberus/tests/test_validation.py", "cerberus/tests/test_normalization.py",
                   "cerberus/tests/test_schema.py"],
    },
    "boltons": {
        "pkg": "boltons", "test_ignore": [],
        "verify": {
            "LOCAL":      {"dev_a": ["tests/test_typeutils.py"],
                           "dev_b": ["tests/test_listutils.py"],
                           "testing": ["tests/test_formatutils.py"]},
            "MODULE":     {"dev_a": ["tests/test_typeutils.py", "tests/test_iterutils.py"],
                           "dev_b": ["tests/test_listutils.py", "tests/test_setutils.py"],
                           "testing": ["tests/test_formatutils.py", "tests/test_strutils.py"]},
            "INTEGRATION":{"dev_a": ["tests/test_typeutils.py", "tests/test_iterutils.py", "tests/test_funcutils.py"],
                           "dev_b": ["tests/test_listutils.py", "tests/test_setutils.py", "tests/test_dictutils.py"],
                           "testing": ["tests/test_formatutils.py", "tests/test_strutils.py", "tests/test_iterutils.py"]},
        },
        "oracle": ["tests/test_iterutils.py", "tests/test_typeutils.py",
                   "tests/test_strutils.py", "tests/test_formatutils.py"],
    },
    "toolz": {
        "pkg": "toolz", "test_ignore": ["toolz/tests/test_package.py"],
        "verify": {
            "LOCAL":      {"dev_a": ["toolz/tests/test_itertoolz.py"],
                           "dev_b": ["toolz/tests/test_recipes.py"],
                           "testing": ["toolz/tests/test_dicttoolz.py"]},
            "MODULE":     {"dev_a": ["toolz/tests/test_itertoolz.py", "toolz/tests/test_functoolz.py"],
                           "dev_b": ["toolz/tests/test_recipes.py", "toolz/tests/test_dicttoolz.py"],
                           "testing": ["toolz/tests/test_dicttoolz.py", "toolz/tests/test_itertoolz.py"]},
            "INTEGRATION":{"dev_a": ["toolz/tests/test_itertoolz.py", "toolz/tests/test_functoolz.py", "toolz/tests/test_dicttoolz.py"],
                           "dev_b": ["toolz/tests/test_recipes.py", "toolz/tests/test_dicttoolz.py", "toolz/tests/test_functoolz.py"],
                           "testing": ["toolz/tests/test_dicttoolz.py", "toolz/tests/test_itertoolz.py", "toolz/tests/test_functoolz.py"]},
        },
        "oracle": ["toolz/tests/test_itertoolz.py", "toolz/tests/test_functoolz.py",
                   "toolz/tests/test_dicttoolz.py", "toolz/tests/test_recipes.py"],
    },
    "pyparsing": {
        "pkg": "pyparsing", "test_ignore": ["tests/test_diagram.py"],
        "verify": {
            "LOCAL":      {"dev_a": ["tests/test_simple_unit.py"],
                           "dev_b": ["tests/test_pep8_converter.py"],
                           "testing": ["tests/test_pre_pep8_deprecation_warnings.py"]},
            "MODULE":     {"dev_a": ["tests/test_simple_unit.py", "tests/test_pep8_converter.py"],
                           "dev_b": ["tests/test_pep8_converter.py", "tests/test_pre_pep8_deprecation_warnings.py"],
                           "testing": ["tests/test_pre_pep8_deprecation_warnings.py", "tests/test_simple_unit.py"]},
            "INTEGRATION":{"dev_a": ["tests/test_simple_unit.py", "tests/test_pep8_converter.py", "tests/test_util.py"],
                           "dev_b": ["tests/test_pep8_converter.py", "tests/test_pre_pep8_deprecation_warnings.py", "tests/test_util.py"],
                           "testing": ["tests/test_pre_pep8_deprecation_warnings.py", "tests/test_simple_unit.py", "tests/test_util.py"]},
        },
        "oracle": ["tests/test_simple_unit.py", "tests/test_util.py",
                   "tests/test_pre_pep8_deprecation_warnings.py"],
    },
}

COMPLETIONS = ("dev_a_completion", "dev_b_completion", "testing_completion")
REGIMES = ("LOCAL", "MODULE", "INTEGRATION")


def _safe_targets(repo, targets):
    """Drop placeholder/None targets."""
    return [t for t in targets if t and "..." not in t]


# ---------------------------------------------------------------------------
# Change registry: auto-select public function contracts per repo, assign kinds
# ---------------------------------------------------------------------------

REPO_TARGET_FILES = {
    "tinydb": ["tinydb/table.py"],
    "cerberus": ["cerberus/validator.py", "cerberus/utils.py"],
    "boltons": ["boltons/typeutils.py", "boltons/iterutils.py"],
    "toolz": ["toolz/itertoolz.py", "toolz/functoolz.py"],
    "pyparsing": ["pyparsing/results.py", "pyparsing/common.py"],
}

# kind rotation: ensures each repo sees NON_SEMANTIC/COMPATIBLE/POTENTIALLY_BREAKING/BREAKING
KIND_ROTATION = ["BODY_ONLY", "ADD_OPTIONAL_PARAM", "CHANGE_RETURN_TYPE",
                 "CHANGE_RETURN_TYPE", "ADD_REQUIRED_PARAM", "BODY_ONLY",
                 "CHANGE_RETURN_TYPE", "ADD_OPTIONAL_PARAM",
                 "CHANGE_RETURN_TYPE", "ADD_REQUIRED_PARAM"]


def _public_funcs(repo, file_rel):
    """Return list of dotted public function/method symbols in the file
    (classes' public methods + top-level funcs), preferring those with a
    return statement for CHANGE_RETURN_TYPE."""
    src = (REPOS_DIR / repo / file_rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            out.append((node.name, _has_return(node)))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and not sub.name.startswith("_") and sub.name not in ("__init__",):
                    out.append((f"{node.name}.{sub.name}", _has_return(sub)))
    return out


def _has_return(fn):
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and n.value is not None:
            return True
    return False


REPO_PREFIX = {"tinydb": "td", "cerberus": "ce", "boltons": "bo",
                "toolz": "tz", "pyparsing": "pp"}


def build_change_registry(per_repo=10):
    """Auto-build a change case per (repo, symbol, kind). Case IDs use unique
    per-repo prefixes to avoid collisions (tinydb/toolz both start with T)."""
    cases = []
    for repo, files in REPO_TARGET_FILES.items():
        syms = []
        for f in files:
            syms.extend((f, s, has_ret) for s, has_ret in _public_funcs(repo, f) if f)
            # dedupe symbol per repo while keeping file mapping
        # pick up to per_repo symbols; prefer ones with returns for variety
        seen = set()
        picked = []
        for f, sym, has_ret in syms:
            if sym in seen:
                continue
            seen.add(sym)
            picked.append((f, sym, has_ret))
        for i in range(min(per_repo, len(picked))):
            f, sym, has_ret = picked[i]
            kind = KIND_ROTATION[i % len(KIND_ROTATION)]
            if kind == "CHANGE_RETURN_TYPE" and not has_ret:
                kind = "ADD_OPTIONAL_PARAM"
            cases.append({
                "case_id": f"{REPO_PREFIX[repo]}c{i+1:02d}",
                "repo": repo, "file": f, "symbol": sym, "kind": kind,
                "artifact": f,
            })
    return cases


# ---------------------------------------------------------------------------
# Calibration: real pytest per (case, completion, regime) + oracle
# ---------------------------------------------------------------------------

CACHE = {}


def _prime_cache_from_disk():
    """Load previously-run calibrations from disk so experiment processes do
    not re-run real pytest. Calibrated outcomes are deterministic for the
    pinned commits, so disk-caching is sound."""
    cal_path = _HERE / "ground_truth" / "calibration.json"
    if not cal_path.exists():
        return
    data = json.load(open(cal_path))
    for regime, rows in data.get("regimes", {}).items():
        for c in rows:
            cid = c.get("case_id")
            if cid:
                CACHE[(cid, regime)] = c


_prime_cache_from_disk()


def calibrate_case(case: dict, regime: str = "LOCAL", use_cache=True) -> dict:
    """Apply the case mutation to a fresh repo copy and run real pytest on each
    completion's verify-set (for `regime`) + the oracle. Returns per-completion
    pass/fail + oracle pass/fail."""
    key = (case["case_id"], regime)
    if use_cache and key in CACHE:
        return CACHE[key]
    repo = case["repo"]
    driver = RepoDriver(repo)
    orig = driver.read(case["file"])
    try:
        new_src = mutate(orig, case["symbol"], case["kind"])
    except Exception as e:   # AssertionError/ValueError from malformed headers
        driver.cleanup()
        res = {"applied": False, "error": f"{type(e).__name__}: {str(e)[:200]}",
               "results": {}}
        CACHE[key] = res
        return res
    if new_src == orig:
        driver.cleanup()
        res = {"applied": False, "error": "no-op mutation",
               "results": {}}
        CACHE[key] = res
        return res
    driver.write(case["file"], new_src)

    sets = REPOS_CFG[repo]["verify"][regime]
    results = {}
    ignore = REPOS_CFG[repo]["test_ignore"]
    for comp, targets in sets.items():
        targets = _safe_targets(repo, targets)
        r = driver.run_pytest(targets, extra=["--ignore=" + ig for ig in ignore]
                              if ignore else [])
        # map short key (dev_a) -> full completion claim id (dev_a_completion)
        results[f"{comp}_completion"] = {"result": r["result"], "tests_failed": r["tests_failed"],
                         "duration_s": r["duration_s"]}
    # oracle
    ora = driver.run_pytest(_safe_targets(repo, REPOS_CFG[repo]["oracle"]),
                            extra=["--ignore=" + ig for ig in ignore] if ignore else [])
    results["oracle"] = {"result": ora["result"], "tests_failed": ora["tests_failed"],
                         "duration_s": ora["duration_s"]}
    driver.cleanup()
    res = {"applied": True, "case_id": case["case_id"], "repo": repo,
           "symbol": case["symbol"], "kind": case["kind"],
           "file": case["file"], "regime": regime, "results": results}
    CACHE[key] = res
    return res


# ---------------------------------------------------------------------------
# Completion gate + invalidation
# ---------------------------------------------------------------------------

GLOBAL_OK = "VERIFIED"
GLOBAL_NOT_READY = "NOT_READY"
GLOBAL_FAILED = "FAILED"


def global_status(claims: dict) -> str:
    st = [c for c in claims.values()]
    if any(c == ClaimStatus.FAILED.value for c in st):
        return GLOBAL_FAILED
    if any(c in (ClaimStatus.STALE.value, ClaimStatus.PENDING.value) for c in st):
        return GLOBAL_NOT_READY
    return GLOBAL_OK


def gt_invalidation(cal: dict, regime: str) -> set:
    """Ground-truth: a completion SHOULD be invalidated iff its verify-set
    FAILED under the change (programmatic, from real pytest)."""
    gt = set()
    for comp in COMPLETIONS:
        res = cal["results"].get(comp, {}).get("result")
        if res == "FAIL":
            gt.add(comp)
    return gt


def is_gfc(cal: dict) -> bool:
    """Global False Completion: all completions' verify-sets PASS while oracle FAILS."""
    if cal["results"].get("oracle", {}).get("result") != "FAIL":
        return False
    return all(cal["results"].get(c, {}).get("result") == "PASS" for c in COMPLETIONS)


# ---------------------------------------------------------------------------
# Ground-truth dependency graph G*  +  dependency instances
# ---------------------------------------------------------------------------

STATIC_SCOPES = frozenset({ChangeType.BREAKING, ChangeType.POTENTIALLY_BREAKING})


def build_gstar_contract(regime: str = "INTEGRATION") -> dict:
    """Contract-level G*: for every (repo, change_case, completion) where the
    calibrated verify-set FAILS, add a CONTRACT->COMPLETION edge from the
    producer's FUNCTION_SIGNATURE contract to that completion. Also static
    FILE->COMPLETION edges (file-level variant)."""
    cases = build_change_registry()
    edges = []
    seen = set()
    for case in cases:
        cal = calibrate_case(case, regime=regime)
        if not cal.get("applied"):
            continue
        ct = _change_type_for_kind(case["kind"])
        failing = [c for c in COMPLETIONS
                   if cal["results"].get(c, {}).get("result") == "FAIL"]
        contract_src = f"{case['file']}::FUNCTION_SIGNATURE::{case['symbol']}"
        for comp in failing:
            for gran, src in [("CONTRACT", contract_src), ("FILE", case["file"])]:
                k = (src, comp, f"{'CONTRACT' if gran=='CONTRACT' else 'ARTIFACT'}->COMPLETION", gran)
                if k in seen:
                    continue
                seen.add(k)
                edges.append(DependencyEdge(
                    source=src, target=comp,
                    relation_type="CONTRACT->COMPLETION" if gran == "CONTRACT" else "ARTIFACT->COMPLETION",
                    scope=frozenset({ct}) if gran == "CONTRACT" else frozenset({ct}),
                    confidence=1.0, provenance=Provenance.MANUAL, granularity=Granularity(gran),
                    note=f"oracle: {case['case_id']} broke {comp}@{regime}"))
    return edges


def _change_type_for_kind(kind: str) -> ChangeType:
    from contracts.mutations import KIND_TO_CHANGE
    return KIND_TO_CHANGE[kind]


def build_dependency_instances(regime: str = "INTEGRATION") -> list:
    """One DependencyInstance per (change_case, completion)."""
    cases = build_change_registry()
    instances = []
    for case in cases:
        cal = calibrate_case(case, regime=regime)
        if not cal.get("applied"):
            continue
        ct = _change_type_for_kind(case["kind"])
        contract_src = f"{case['file']}::FUNCTION_SIGNATURE::{case['symbol']}"
        for comp in COMPLETIONS:
            obl = f"OBL::{case['case_id']}::{comp}"
            failing = cal["results"].get(comp, {}).get("result") == "FAIL"
            gt_rel = f"{contract_src}->{comp}" if failing else "NONE"
            instances.append({
                "instance_id": f"{case['case_id']}::{comp}",
                "repo": case["repo"], "producer_task": f"task-{case['case_id']}",
                "producer_artifact": case["file"], "producer_contract": contract_src,
                "consumer_task": f"task-{comp}", "consumer_completion": comp,
                "verification_obligation": obl,
                "change": {"case_id": case["case_id"], "symbol": case["symbol"],
                           "kind": case["kind"], "change_type": ct.value},
                "ground_truth_relation": gt_rel,
                "gt_should_invalidate": failing,
                "oracle_result": cal["results"].get("oracle", {}).get("result"),
                "is_gfc": is_gfc(cal),
            })
    return instances


if __name__ == "__main__":
    reg = build_change_registry()
    print(f"change cases: {len(reg)}")
    from collections import Counter
    print("by repo:", Counter(c["repo"] for c in reg))
    print("by kind:", Counter(c["kind"] for c in reg))
    # quick single-case calibration smoke
    cal = calibrate_case(reg[0], regime="LOCAL")
    print("smoke cal:", cal["case_id"], {k: v["result"] for k, v in cal["results"].items()})