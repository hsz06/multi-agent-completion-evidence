"""Real change-case definitions for the three repos.

Each case is a REAL modification of a repo source file (string transform on the
pinned commit). Ground truth is NOT guessed: the oracle calibration script runs
the modified tree through real pytest and records which downstream test sets
fail. All experiments then replay against the calibrated result.

Agent observation sets (per repo) are fixed so that:
  - agent B's verification tends to pass even when a producer change breaks the
    product contract (realistic: a consumer's own smoke tests are narrow);
  - the oracle (final integration suite) is what exposes the real break.

CLAIM SLOTS (CompletionClaims in this PoC):
  agent_a_complete | agent_b_complete | testing_complete
"""
from __future__ import annotations

import json

REPO_VERIFY_SETS = {
    "tinydb": {
        "agent_a": ["tests/test_queries.py"],          # exercises queries/tinydb internals
        "agent_b": ["tests/test_utils.py"],            # LRUCache — independent of Table
        "testing": ["tests/test_operations.py"],       # insert/update path
        "oracle": ["tests/test_tinydb.py", "tests/test_tables.py"],
    },
    "cerberus": {
        "agent_a": ["cerberus/tests/test_schema.py"],
        "agent_b": ["cerberus/tests/test_utils.py"],
        "testing": ["cerberus/tests/test_normalization.py"],
        "oracle": ["cerberus/tests/test_validation.py"],
    },
    "boltons": {
        "agent_a": ["tests/test_typeutils.py"],
        "agent_b": ["tests/test_iterutils.py"],
        "testing": ["tests/test_listutils.py"],
        "oracle": ["tests/"],
    },
}

# Extended verify-set config used for the "testable GFC" arm of Phase 2B:
# testing_completion also covers test_tables.py (realistic — the integration
# test agent owns table-level contract tests). This makes some GFC cases
# recoverable via a missing ARTIFACT->COMPLETION edge, while the original
# (oracle-only) config stays as the "unrecoverable" control arm.
REPO_VERIFY_SETS_EXTENDED = {
    "tinydb": {
        "agent_a": ["tests/test_queries.py"],
        "agent_b": ["tests/test_utils.py"],
        "testing": ["tests/test_operations.py", "tests/test_tables.py"],
        "oracle": ["tests/test_tinydb.py", "tests/test_tables.py"],
    },
    "cerberus": {
        "agent_a": ["cerberus/tests/test_schema.py"],
        "agent_b": ["cerberus/tests/test_utils.py"],
        "testing": ["cerberus/tests/test_normalization.py",
                    "cerberus/tests/test_validation.py"],
        "oracle": ["cerberus/tests/test_validation.py"],
    },
    "boltons": {
        "agent_a": ["tests/test_typeutils.py"],
        "agent_b": ["tests/test_iterutils.py"],
        "testing": ["tests/test_listutils.py", "tests/test_iterutils.py"],
        "oracle": ["tests/"],
    },
}

CLAIM_SLOTS = ("agent_a_completion", "agent_b_completion", "testing_completion")


# ===========================================================================
# Transforms per repo. Each fn: orig_text -> new_text (deterministic).
# ===========================================================================

def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) >= 1, f"pattern not found:\n{old}"
    assert text.count(old) == 1, f"pattern not unique ({text.count(old)}):\n{old}"
    return text.replace(old, new)


# ------------------------------ tinydb --------------------------------------

def tinydb_impl_internal(src: str) -> str:
    # implementation-only: rewrite list(iter(self)) with equivalent list comp
    return _replace_once(
        src,
        "        return list(iter(self))",
        "        return [x for x in iter(self)]\n")


def tinydb_all_returns_dict(src: str) -> str:
    # BREAKING public API: all() now returns a mapping keyed by doc_id
    return _replace_once(
        src,
        "        return list(iter(self))",
        "        return {doc.doc_id: doc for doc in iter(self)}\n")


def tinydb_insert_required_param(src: str) -> str:
    # BREAKING signature: insert gains a required kwarg
    return _replace_once(
        src,
        "    def insert(self, document: Mapping) -> int:",
        "    def insert(self, document: Mapping, *, required_tag: int) -> int:")


def tinydb_version_bump(src: str) -> str:
    # version bump: additive metadata, no API contract change
    return _replace_once(src, "__version__ = '4.9.0'", "__version__ = '4.9.0.post1'")


def tinydb_add_optional_method(src: str) -> str:
    # additive: new public method with no side effect on existing behaviour
    return _replace_once(
        src,
        "    def all(self) -> list[Document]:",
        "    def count_nonmatching(self) -> int:\n"
        "        return 0\n\n"
        "    def all(self) -> list[Document]:")


def tinydb_document_required_attr(src: str) -> str:
    # BREAKING: Document constructor gains a required positional parameter
    return _replace_once(
        src,
        "    def __init__(self, value: Mapping, doc_id: int):",
        "    def __init__(self, value: Mapping, doc_id: int, created_by: str):")


# ------------------------------ cerberus -------------------------------------

def cerberus_utils_internal(src: str) -> str:
    # implementation-only: whitespace-equivalent body rewrite of internal helper
    return _replace_once(
        src,
        "def drop_item_from_tuple(t, i):\n    return t[:i] + t[i + 1 :]",
        "def drop_item_from_tuple(t, i):\n    return t[:i] + t[i + 1:]  # same semantics")


def cerberus_validate_required_param(src: str) -> str:
    # BREAKING: validate() gains a required kwarg `strict_required`
    return _replace_once(
        src,
        "    def validate(self, document, schema=None, update=False, normalize=True):",
        "    def validate(self, document, schema=None, update=False, normalize=True,\n"
        "                 strict_required=False):")


def cerberus_remove_allowed_rule(src: str) -> str:
    # BREAKING: drop the public validation rule `allowed`
    return _replace_once(
        src,
        "    def _validate_allowed(self, allowed_values, field, value):",
        "    def _removed_allowed_rule(self, allowed_values, field, value):")


def cerberus_platform_constant(src: str) -> str:
    # compat config change: widens internal int alias, behaviour effectively same
    return _replace_once(
        src,
        "    _int_types = (int,)",
        "    _int_types = (int, bool)")


def cerberus_add_new_rule(src: str) -> str:
    # additive: register a brand-new validation rule method
    return _replace_once(
        src,
        "    def _validate_contains(self, expected_values, field, value):",
        "    def _validate_oddness(self, odd, field, value):\n"
        "        del field\n        if odd % 2 == 0:\n"
        "            self._error('oddness')\n\n\n"
        "    def _validate_contains(self, expected_values, field, value):")


# ------------------------------ boltons --------------------------------------

def boltons_sentinel_internal(src: str) -> str:
    # implementation-only: same behaviour, refactored repr guard
    return _replace_once(
        src,
        "        def __bool__(self):\n            return False",
        "        def __bool__(self):\n            # sentinels are always falsy\n            return False")


def boltons_sentinel_required_param(src: str) -> str:
    # BREAKING: make_sentinel requires name
    return _replace_once(
        src,
        "def make_sentinel(name='_MISSING', var_name=None):",
        "def make_sentinel(name, var_name=None):")


def boltons_sentinel_optional_param(src: str) -> str:
    # compatible: additive kwarg
    return _replace_once(
        src,
        "def make_sentinel(name='_MISSING', var_name=None):",
        "def make_sentinel(name='_MISSING', var_name=None, *, pickleable=False):")


def boltons_isiterable_internal(src: str) -> str:
    # implementation-only in iterutils
    return _replace_once(
        src,
        "def is_iterable(obj):",
        "def is_iterable(obj):  # rewritten internally")


def boltons_rename_isiterable(src: str) -> str:
    # BREAKING public rename
    return _replace_once(
        src,
        "def is_iterable(obj):",
        "def is_an_iterable(obj):")


def boltons_remove_chunked(src: str) -> str:
    # BREAKING public rename of a helper used by consumers
    return _replace_once(
        src,
        "def chunked(src, size, count=None, **kw):",
        "def _chunked_internal(src, size, count=None, **kw):")


# ===========================================================================
# Case registry
# ===========================================================================

CASES = [
    # ---------------- tinydb -------------------------------
    {"repo": "tinydb", "case_id": "T1_impl_internal",
     "producer": "tinydb/table.py", "owner": "agent-a",
     "transform": tinydb_impl_internal, "ct": "NON_SEMANTIC",
     "note": "Table.all() rewritten equivalently"},
    {"repo": "tinydb", "case_id": "T2_all_breaks",
     "producer": "tinydb/table.py", "owner": "agent-a",
     "transform": tinydb_all_returns_dict, "ct": "BREAKING",
     "note": "Table.all() return type list->dict"},
    {"repo": "tinydb", "case_id": "T3_insert_sig",
     "producer": "tinydb/table.py", "owner": "agent-a",
     "transform": tinydb_insert_required_param, "ct": "BREAKING",
     "note": "insert() gains required kwarg"},
    {"repo": "tinydb", "case_id": "T4_version_cfg",
     "producer": "tinydb/version.py", "owner": "agent-a",
     "transform": tinydb_version_bump, "ct": "COMPATIBLE",
     "note": "__version__ bumped, no API change"},
    {"repo": "tinydb", "case_id": "T5_add_method",
     "producer": "tinydb/table.py", "owner": "agent-a",
     "transform": tinydb_add_optional_method, "ct": "COMPATIBLE",
     "note": "additive public method"},
    {"repo": "tinydb", "case_id": "T6_doc_required_attr",
     "producer": "tinydb/table.py", "owner": "agent-a",
     "transform": tinydb_document_required_attr, "ct": "BREAKING",
     "note": "Document constructor requires created_by"},

    # ---------------- cerberus ----------------------------
    {"repo": "cerberus", "case_id": "C1_utils_internal",
     "producer": "cerberus/utils.py", "owner": "agent-a",
     "transform": cerberus_utils_internal, "ct": "NON_SEMANTIC",
     "note": "internal tuple helper rewritten"},
    {"repo": "cerberus", "case_id": "C2_validate_sig",
     "producer": "cerberus/validator.py", "owner": "agent-a",
     "transform": cerberus_validate_required_param, "ct": "BREAKING",
     "note": "validate() adds required kwarg"},
    {"repo": "cerberus", "case_id": "C3_remove_allowed",
     "producer": "cerberus/validator.py", "owner": "agent-a",
     "transform": cerberus_remove_allowed_rule, "ct": "BREAKING",
     "note": "allowed rule removed"},
    {"repo": "cerberus", "case_id": "C4_platform_const",
     "producer": "cerberus/platform.py", "owner": "agent-a",
     "transform": cerberus_platform_constant, "ct": "COMPATIBLE",
     "note": "int alias widened"},
    {"repo": "cerberus", "case_id": "C5_add_new_rule",
     "producer": "cerberus/validator.py", "owner": "agent-a",
     "transform": cerberus_add_new_rule, "ct": "COMPATIBLE",
     "note": "additive new validation rule"},

    # ---------------- boltons -----------------------------
    {"repo": "boltons", "case_id": "B1_sentinel_internal",
     "producer": "boltons/typeutils.py", "owner": "agent-a",
     "transform": boltons_sentinel_internal, "ct": "NON_SEMANTIC",
     "note": "sentinel __bool__ comment+impl"},
    {"repo": "boltons", "case_id": "B2_sentinel_required",
     "producer": "boltons/typeutils.py", "owner": "agent-a",
     "transform": boltons_sentinel_required_param, "ct": "BREAKING",
     "note": "make_sentinel requires name"},
    {"repo": "boltons", "case_id": "B3_sentinel_optional",
     "producer": "boltons/typeutils.py", "owner": "agent-a",
     "transform": boltons_sentinel_optional_param, "ct": "COMPATIBLE",
     "note": "additive kwarg"},
    {"repo": "boltons", "case_id": "B4_rename_isiterable",
     "producer": "boltons/iterutils.py", "owner": "agent-a",
     "transform": boltons_rename_isiterable, "ct": "BREAKING",
     "note": "is_iterable renamed"},
    {"repo": "boltons", "case_id": "B5_remove_chunked",
     "producer": "boltons/iterutils.py", "owner": "agent-a",
     "transform": boltons_remove_chunked, "ct": "BREAKING",
     "note": "chunked renamed"},
]

CASE_IDX = {(c["repo"], c["case_id"]): c for c in CASES}

ORACLE_REPO = "tinydb"   # used for coverage sensitivity experiment


def load_case(repo: str, case_id: str) -> dict:
    return CASE_IDX[(repo, case_id)]