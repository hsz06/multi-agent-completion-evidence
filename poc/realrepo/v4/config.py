"""v4 config: verification pool per repo + reuse of v3 engine pieces.

Pools are restricted to fast, stable test files (pyparsing excludes the 26s
test_unit.py and import-erroring test_diagram.py; boltons/toolz exclude env-
sensitive files). Pool choice is fixed and does not change with results.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # realrepo (v2 common, v3)
sys.path.insert(0, str(_HERE.parent / "v3"))   # v3

from engine import REPOS_CFG, REPOS_DIR, build_change_registry

# Per-repo verification pool (test-file granularity). Each entry is a repo-
# relative path. These are the AVAILABLE obligations an agent could select.
POOL_FILES = {
    "tinydb": [
        "tests/test_tinydb.py", "tests/test_utils.py", "tests/test_tables.py",
        "tests/test_operations.py", "tests/test_queries.py",
        "tests/test_storages.py", "tests/test_middlewares.py",
    ],
    "cerberus": [
        "cerberus/tests/test_utils.py", "cerberus/tests/test_validation.py",
        "cerberus/tests/test_registries.py", "cerberus/tests/test_errors.py",
        "cerberus/tests/test_schema.py", "cerberus/tests/test_assorted.py",
        "cerberus/tests/test_legacy.py", "cerberus/tests/test_customization.py",
        "cerberus/tests/test_normalization.py",
    ],
    "boltons": [
        "tests/test_typeutils.py", "tests/test_iterutils.py",
        "tests/test_funcutils.py", "tests/test_strutils.py",
        "tests/test_formatutils.py", "tests/test_listutils.py",
        "tests/test_dictutils.py", "tests/test_setutils.py",
        "tests/test_cacheutils.py", "tests/test_jsonutils.py",
    ],
    "toolz": [
        "toolz/tests/test_itertoolz.py", "toolz/tests/test_functoolz.py",
        "toolz/tests/test_dicttoolz.py", "toolz/tests/test_recipes.py",
        "toolz/tests/test_curried.py", "toolz/tests/test_compatibility.py",
        "toolz/tests/test_signatures.py", "toolz/tests/test_utils.py",
    ],
    "pyparsing": [
        "tests/test_simple_unit.py", "tests/test_util.py",
        "tests/test_testing.py", "tests/test_pep8_converter.py",
        "tests/test_pre_pep8_deprecation_warnings.py",
    ],
}

# default LOCAL existing verify-set per completion (mirrors v3 LOCAL)
EXISTING_VERIFY_SET = {
    repo: REPOS_CFG[repo]["verify"]["LOCAL"] for repo in REPOS_CFG
}


def pool_for(repo: str) -> list[str]:
    return list(POOL_FILES[repo])


def all_cases():
    return build_change_registry()


if __name__ == "__main__":
    for r in POOL_FILES:
        print(f"{r}: {len(POOL_FILES[r])} pool files | existing LOCAL: {EXISTING_VERIFY_SET[r]}")