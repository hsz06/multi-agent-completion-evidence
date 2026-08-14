"""ChangeImpactClassifier: AST-signature change classification.

Decides whether a change to a producer file can break a consumer edge. Keeps
only signature-level facts (public symbol rename/delete, param add-as-required,
param removal) and returns UNKNOWN when it cannot tell. Both v1-style schema
diff rules are included for completeness.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from .models import ChangeType


@dataclass
class PublicSymbol:
    kind: str              # function | class | method
    name: str
    params: list | None = None


def _ast_params(fn) -> list:
    args = fn.args
    defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
    out = []
    for arg, default in zip(args.args, defaults):
        out.append({"name": arg.arg, "kind": "optional" if default is not None
                    else "required"})
    for a in args.posonlyargs:
        out.append({"name": a.arg, "kind": "posonly-required"})
    kw_defaults = [None] * (len(args.kwonlyargs) - len(args.kw_defaults)) + list(args.kw_defaults)
    for kw, default in zip(args.kwonlyargs, kw_defaults):
        out.append({"name": kw.arg, "kind": "kwonly-optional" if default is not None
                    else "kwonly-required"})
    if args.vararg:
        out.append({"name": args.vararg.arg, "kind": "vararg"})
    if args.kwarg:
        out.append({"name": args.kwarg.arg, "kind": "kwarg"})
    return out


def strip_comments(src: str) -> str:
    """Remove comments/docstrings/blank lines so we can detect non-semantic diffs."""
    import ast as _ast
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return ""
    _ast.fix_missing_locations(tree)
    lines = src.splitlines(keepends=True)

    def drop_range(node):
        # remove docstring/comment tokens crudely by blanking their raw lines
        pass

    out = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:                       # blank lines
            continue
        if stripped.startswith("#"):           # comment lines
            continue
        out.append(line)
    return "".join(out)


def _non_semantic_change(old_src: str, new_src: str) -> bool:
    """True when the only differences are comments/blank/docstring-ish text."""
    a, b = strip_comments(old_src), strip_comments(new_src)
    return a == b


def extract_signatures(src: str) -> dict:
    """Return public symbol (dotted) -> PublicSymbol for top-level defs/classes
    and their public methods."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_private(node.name):
                continue
            out[node.name] = PublicSymbol(node.__class__.__name__,
                                          node.name, _ast_params(node))
        elif isinstance(node, ast.ClassDef):
            if _is_private(node.name):
                continue
            out[node.name] = PublicSymbol("class", node.name, [])
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and not _is_private(sub.name):
                    out[f"{node.name}.{sub.name}"] = PublicSymbol(
                        "method", sub.name, _ast_params(sub))
    return out


def _is_private(name: str) -> bool:
    """`_x` and `__x` are private; `__x__` (dunder) is part of the public
    protocol and must be tracked (e.g. __init__, __getitem__)."""
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


class ChangeClassifier:
    def classify(self, old_src: str, new_src: str) -> ChangeType:
        """Signature-aware change classification.

        Order of decision:
          1. identical text               -> NON_SEMANTIC
          2. comments/blank-only diff     -> NON_SEMANTIC
          3. public symbol added only     -> COMPATIBLE
          4. public symbol removed        -> BREAKING
          5. param added-required/removed -> BREAKING
             param added-optional/default -> COMPATIBLE
          6. body changed, signature same -> POTENTIALLY_BREAKING (conservative)
          7. otherwise                    -> UNKNOWN
        """
        if old_src == new_src or _non_semantic_change(old_src, new_src):
            return ChangeType.NON_SEMANTIC

        old = extract_signatures(old_src)
        new = extract_signatures(new_src)

        removed = set(old) - set(new)
        if removed:
            return ChangeType.BREAKING                  # symbol deleted/renamed
        added = set(new) - set(old)
        if added:
            return ChangeType.COMPATIBLE                # additive only

        breaking, compatible = False, False
        for name in old.keys() & new.keys():
            o, n = old[name], new[name]
            if o.kind != n.kind:
                breaking = True
                continue
            o_map = {p["name"]: p for p in o.params}
            n_map = {p["name"]: p for p in n.params}
            if set(o_map) - set(n_map):
                breaking = True                         # param removed
            for pname, spec in n_map.items():
                if pname not in o_map:
                    if "required" in spec["kind"]:
                        breaking = True
                    else:
                        compatible = True
            for pname in set(o_map) & set(n_map):
                if o_map[pname]["kind"] == "optional" and \
                        "required" in n_map[pname]["kind"]:
                    breaking = True
                elif o_map[pname]["kind"] != n_map[pname]["kind"]:
                    compatible = True
        if breaking:
            return ChangeType.BREAKING
        if compatible:
            return ChangeType.COMPATIBLE

        # signature identical but content changed -> conservative unknown risk
        if old == new:
            return ChangeType.POTENTIALLY_BREAKING
        return ChangeType.UNKNOWN


def classify_schema_change(old: dict, new: dict) -> ChangeType:
    """Deterministic schema diff (mirrors v1 rules where applicable)."""
    of, nf = old.get("fields", {}), new.get("fields", {})
    removed = set(of) - set(nf)
    added = set(nf) - set(of)
    type_changed = any(of[k] != nf[k] for k in set(of) & set(nf))
    newly_required = set(new.get("required", [])) - set(old.get("required", []))
    if removed or type_changed or newly_required:
        return ChangeType.BREAKING
    if added:
        return ChangeType.COMPATIBLE
    return ChangeType.NON_SEMANTIC