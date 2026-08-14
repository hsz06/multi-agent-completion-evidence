"""v4.2 Phase A: Private Contract Extraction.

Extends the contract graph with PRIVATE_BEHAVIOR_CONTRACT for underscore
symbols that satisfy at least one of:
  A. symbol appears in the changed diff (changed_symbol)
  B. symbol is dynamically covered by >=1 available pool test
  C. symbol is called by a public ContractNode (public-call-path)

Never adds arbitrary private helpers — only those with a real signal.
Does NOT read the held-out oracle.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent / "v3"))   # v3
sys.path.insert(0, str(_HERE))                 # v4

from contracts.extractor import ContractExtractor
from contracts.model import ContractNode, ContractType
from engine import REPOS_DIR, build_change_registry, REPOS_CFG
from config import POOL_FILES
from obligation.line_cov import coverage_lines


@dataclass
class PrivateContractNode:
    contract_id: str
    symbol: str
    file: str
    line_range: tuple
    callers: list = field(default_factory=list)
    public_ancestors: list = field(default_factory=list)
    coverage_tests: list = field(default_factory=list)
    extraction_reason: str = ""   # CHANGED_SYMBOL|DYNAMICALLY_COVERED|PUBLIC_CALL_PATH|MULTI_SIGNAL

    def to_contract_node(self) -> ContractNode:
        return ContractNode(
            contract_id=self.contract_id, artifact_id=self.file,
            contract_type=ContractType("PRIVATE_BEHAVIOR_CONTRACT"),
            symbol=self.symbol, signature={"private": True},
            location=f"{self.file}:{self.line_range[0]}",
        )

    def to_dict(self):
        return asdict(self)


_SYMBOL_RANGE_CACHE: dict = {}


def _symbol_range(repo: str, artifact: str, symbol: str):
    key = (repo, artifact, symbol)
    if key in _SYMBOL_RANGE_CACHE:
        return _SYMBOL_RANGE_CACHE[key]
    p = REPOS_DIR / repo / artifact
    if not p.exists():
        _SYMBOL_RANGE_CACHE[key] = None
        return None
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except Exception:
        _SYMBOL_RANGE_CACHE[key] = None
        return None
    parts = symbol.split(".")
    rng = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == parts[0] and len(parts) == 1:
            rng = (node.lineno, getattr(node, "end_lineno", node.lineno))
            break
        if isinstance(node, ast.ClassDef) and node.name == parts[0] and len(parts) == 2:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and sub.name == parts[-1]:
                    rng = (sub.lineno, getattr(sub, "end_lineno", sub.lineno))
                    break
            if rng:
                break
    _SYMBOL_RANGE_CACHE[key] = rng
    return rng


def _private_symbols(repo: str, artifact: str) -> list:
    """All underscore-prefixed (but not dunder) functions/methods in a file,
    as dotted symbols with their line ranges."""
    p = REPOS_DIR / repo / artifact
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name.startswith("_") and not node.name.startswith("__"):
            out.append((node.name, node.lineno, getattr(node, "end_lineno", node.lineno)))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        sub.name.startswith("_") and not sub.name.startswith("__"):
                    out.append((f"{node.name}.{sub.name}", sub.lineno,
                                getattr(sub, "end_lineno", sub.lineno)))
    return out


def _callers_of(repo: str, artifact: str, sym_leaf: str) -> list:
    """Files/function names in the repo that call `sym_leaf`. Cheap AST scan."""
    out = []
    ext = ContractExtractor(REPOS_DIR / repo, repo)
    for src_file in ext.source_files():
        try:
            tree = ast.parse(src_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call):
                        f = call.func
                        name = f.attr if isinstance(f, ast.Attribute) else (
                            f.id if isinstance(f, ast.Name) else None)
                        if name == sym_leaf:
                            rel = str(src_file.relative_to(REPOS_DIR / repo))
                            out.append(f"{rel}::{fn.name}")
    return sorted(set(out))


def extract_private_contracts(repo: str, changed_symbols_by_file: dict = None) -> list:
    """Extract PRIVATE_BEHAVIOR_CONTRACT nodes for `repo`. changed_symbols_by_file
    maps artifact -> set of changed dotted symbols (for CHANGED_SIGNAL reason)."""
    changed_symbols_by_file = changed_symbols_by_file or {}
    nodes = []
    ext = ContractExtractor(REPOS_DIR / repo, repo)
    # collect all source files of this repo's package
    for src_file in ext.source_files():
        artifact = str(src_file.relative_to(REPOS_DIR / repo))
        changed_syms = changed_symbols_by_file.get(artifact, set())
        for sym, ln, end in _private_symbols(repo, artifact):
            reasons = set()
            if sym in changed_syms or sym.split(".")[-1] in {s.split(".")[-1] for s in changed_syms}:
                reasons.add("CHANGED_SYMBOL")
            # dynamically covered by any pool test? (line-level)
            for tf in POOL_FILES[repo]:
                lm = coverage_lines(repo, tf)
                executed = lm.get(artifact, set())
                if any(ln + 1 <= e <= end for e in executed):  # body line executed
                    reasons.add("DYNAMICALLY_COVERED")
                    break
            # public-call-path: called by a public symbol
            callers = _callers_of(repo, artifact, sym.split(".")[-1])
            public_callers = [c for c in callers if not c.split("::")[-1].startswith("_")]
            if public_callers:
                reasons.add("PUBLIC_CALL_PATH")
            if not reasons:
                continue
            reason = "MULTI_SIGNAL" if len(reasons) > 1 else next(iter(reasons))
            nodes.append(PrivateContractNode(
                contract_id=f"{artifact}::PRIVATE_BEHAVIOR_CONTRACT::{sym}",
                symbol=sym, file=artifact, line_range=(ln, end),
                callers=callers, public_ancestors=public_callers,
                coverage_tests=[], extraction_reason=reason,
            ))
    return nodes


if __name__ == "__main__":
    # which cerberus changed symbols are private?
    cases = build_change_registry()
    by_file = {}
    for c in cases:
        by_file.setdefault(c["repo"], {}).setdefault(c["file"], set()).add(c["symbol"])
    for repo in ("cerberus",):
        cs = by_file.get(repo, {})
        flat = {f: set() for f in cs}
        pcs = extract_private_contracts(repo, flat)
        print(f"{repo}: {len(pcs)} PRIVATE_BEHAVIOR_CONTRACT nodes")
        for p in pcs[:8]:
            print(f"  {p.symbol} [{p.extraction_reason}] callers={p.callers[:2]}")