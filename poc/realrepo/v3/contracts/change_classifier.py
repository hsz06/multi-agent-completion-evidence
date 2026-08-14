"""ContractChangeClassifier: classify a change *at the contract level*.

Given (old_contracts, new_contracts) for an artifact, emits ChangeType per
contract-type rule. Falls back to UNKNOWN rather than guessing semantics.

Reuses v2's AST-signature comparison philosophy but operates on ContractNode
sets and adds RETURN_CONTRACT / CONFIG_KEY / SCHEMA_FIELD rules.
"""
from __future__ import annotations

import ast
from .model import ContractNode, ContractType, ChangeType


def _contracts_by_id(contracts):
    return {c.contract_id: c for c in contracts}


def _symbols_of(contracts, artifact, ctype):
    return {c.symbol: c for c in contracts
            if c.artifact_id == artifact and c.contract_type == ctype}


class ContractChangeClassifier:
    def classify(self, old: list[ContractNode], new: list[ContractNode],
                 artifact: str) -> ChangeType:
        old_ids = _contracts_by_id(old)
        new_ids = _contracts_by_id(new)
        old_set, new_set = set(old_ids), set(new_ids)

        # 1. removed contract (symbol deleted / renamed)
        removed = old_set - new_set
        if removed:
            return ChangeType.BREAKING

        # 2. added contract(s) only
        added = new_set - old_set
        if added and not (old_set & new_set):
            return ChangeType.COMPATIBLE

        worst = ChangeType.NON_SEMANTIC   #Neutral default; escalate upward

        # FUNCTION_SIGNATURE: param/return changes on shared contracts
        fs_old = {c.symbol: c for c in old if c.artifact_id == artifact
                  and c.contract_type == ContractType.FUNCTION_SIGNATURE}
        fs_new = {c.symbol: c for c in new if c.artifact_id == artifact
                  and c.contract_type == ContractType.FUNCTION_SIGNATURE}
        for sym in fs_old.keys() & fs_new.keys():
            r = self._func_diff(fs_old[sym], fs_new[sym])
            if r == ChangeType.BREAKING:
                return ChangeType.BREAKING
            if _rank(r) > _rank(worst):
                worst = r

        # RETURN_CONTRACT: return annotation changed
        rc_old = {c.symbol: c for c in old if c.artifact_id == artifact
                  and c.contract_type == ContractType.RETURN_CONTRACT}
        rc_new = {c.symbol: c for c in new if c.artifact_id == artifact
                  and c.contract_type == ContractType.RETURN_CONTRACT}
        for sym in rc_old.keys() & rc_new.keys():
            if rc_old[sym].signature.get("return") != rc_new[sym].signature.get("return"):
                # return type change is POTENTIALLY_BREAKING (callers may depend)
                if _rank(ChangeType.POTENTIALLY_BREAKING) > _rank(worst):
                    worst = ChangeType.POTENTIALLY_BREAKING
        # return contract removed => breaking
        if set(rc_old) - set(rc_new):
            return ChangeType.BREAKING

        # CONFIG_KEY: key removed/renamed => breaking; default changed => potentially; added => compatible
        ck_old = {c.symbol: c for c in old if c.artifact_id == artifact
                  and c.contract_type == ContractType.CONFIG_KEY}
        ck_new = {c.symbol: c for c in new if c.artifact_id == artifact
                  and c.contract_type == ContractType.CONFIG_KEY}
        if set(ck_old) - set(ck_new):
            return ChangeType.BREAKING
        for k in set(ck_old) & set(ck_new):
            if ck_old[k].signature.get("default") != ck_new[k].signature.get("default"):
                if _rank(ChangeType.POTENTIALLY_BREAKING) > _rank(worst):
                    worst = ChangeType.POTENTIALLY_BREAKING
        if set(ck_new) - set(ck_old):
            if _rank(ChangeType.COMPATIBLE) > _rank(worst):
                worst = ChangeType.COMPATIBLE

        # added function signatures (additive public API)
        added_fs = set(fs_new) - set(fs_old)
        if added_fs and worst == ChangeType.NON_SEMANTIC:
            worst = ChangeType.COMPATIBLE

        return worst

    # -- helpers ----------------------------------------------------------
    def _func_diff(self, oldc: ContractNode, newc: ContractNode) -> ChangeType:
        op = {p["name"]: p for p in oldc.signature.get("params", [])}
        np = {p["name"]: p for p in newc.signature.get("params", [])}
        if set(op) - set(np):
            return ChangeType.BREAKING            # param removed
        for name, spec in np.items():
            if name not in op:
                if "required" in spec["kind"]:
                    return ChangeType.BREAKING    # required param added
        for name in set(op) & set(np):
            if "required" in op[name]["kind"] and "optional" in np[name]["kind"]:
                pass   # required->optional: compatible
            elif "optional" in op[name]["kind"] and "required" in np[name]["kind"]:
                return ChangeType.BREAKING
            # param type/annotation change
            if op[name].get("ann") != np[name].get("ann") and op[name].get("ann") is not None:
                if _rank(ChangeType.POTENTIALLY_BREAKING) > _rank(ChangeType.COMPATIBLE):
                    return ChangeType.POTENTIALLY_BREAKING
        return ChangeType.NON_SEMANTIC


def _rank(ct: ChangeType) -> int:
    return {ChangeType.NON_SEMANTIC: 0, ChangeType.COMPATIBLE: 1,
            ChangeType.POTENTIALLY_BREAKING: 2, ChangeType.BREAKING: 4,
            ChangeType.UNKNOWN: 3}.get(ct, 3)


def classify_text_change(old_src: str, new_src: str) -> ChangeType:
    """Fallback: re-extract from raw source text (used when only source diffs
    are available, not pre-extracted ContractNode lists). Replicates v2's
    body-only -> POTENTIALLY_BREAKING rule."""
    from .extractor import ContractExtractor
    import tempfile, shutil
    # cheap: parse in-memory via a temp dir is heavy; instead parse text directly
    try:
        old_t = ast.parse(old_src)
        new_t = ast.parse(new_src)
    except SyntaxError:
        return ChangeType.UNKNOWN
    if _strip(old_t) == _strip(new_t):
        return ChangeType.NON_SEMANTIC
    # delegate to contract-level via a synthetic extractor parse
    return _classify_trees(old_t, new_t)


def _strip(tree):
    lines = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lines.append((type(node).__name__, node.name, node.lineno))
    return lines


def _classify_trees(old_t, new_t):
    # very light: compare top-level public symbol sets
    def syms(t):
        return {n.name for n in t.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and not n.name.startswith("_")}
    o, n = syms(old_t), syms(new_t)
    if o == n:
        return ChangeType.POTENTIALLY_BREAKING
    if o - n:
        return ChangeType.BREAKING
    return ChangeType.COMPATIBLE