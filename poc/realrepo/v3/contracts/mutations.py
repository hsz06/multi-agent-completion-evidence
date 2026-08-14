"""Contract-level mutation engine.

Given a producer artifact + a FUNCTION_SIGNATURE contract symbol, produce a
real source transform (old_src -> new_src) for a mutation kind. Mutations are
local to the target function and require no multi-file rewrite, so the
calibrated pytest outcome is deterministic.

Mutation kinds:
  BODY_ONLY              -> NON_SEMANTIC         (equivalent body rewrite)
  CHANGE_RETURN_TYPE     -> POTENTIALLY_BREAKING (return value shape change)
  ADD_REQUIRED_PARAM     -> BREAKING             (new required kwonly/positional)
  ADD_OPTIONAL_PARAM     -> COMPATIBLE           (new optional param)
  REMOVE_PARAM           -> BREAKING             (drop a non-self param)
"""
from __future__ import annotations

import ast
from .model import ChangeType


KIND_TO_CHANGE = {
    "BODY_ONLY": ChangeType.NON_SEMANTIC,
    "CHANGE_RETURN_TYPE": ChangeType.POTENTIALLY_BREAKING,
    "ADD_REQUIRED_PARAM": ChangeType.BREAKING,
    "ADD_OPTIONAL_PARAM": ChangeType.COMPATIBLE,
    "REMOVE_PARAM": ChangeType.BREAKING,
}


def _find_func(tree, symbol: str):
    """Locate the FunctionDef for a dotted symbol like 'Table.insert' or 'remove'.
    Returns (node, owner_class_node_or_None)."""
    parts = symbol.split(".")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[0] and len(parts) == 1:
            return node, None
        if isinstance(node, ast.ClassDef) and len(parts) == 2 and node.name == parts[0]:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == parts[1]:
                    return sub, node
    return None, None


def _line(src_lines, lineno):
    return src_lines[lineno - 1]


def _replace_def_line(src_lines, fn, new_def):
    """Replace the (possibly multi-line) def signature of `fn` with `new_def`."""
    start = fn.lineno - 1
    end = (fn.body[0].lineno - 1) if fn.body else start + 1
    src_lines[start:end] = [new_def + "\n"]


def mutate(src: str, symbol: str, kind: str) -> str:
    tree = ast.parse(src)
    fn, owner = _find_func(tree, symbol)
    if fn is None:
        raise AssertionError(f"symbol {symbol} not found")
    lines = src.splitlines(keepends=True)

    if kind == "BODY_ONLY":
        # Safe non-semantic mutation: append an indented comment line just
        # after the function's last line. Comments never affect parsing, so
        # this cannot break indentation (unlike touching multi-line returns).
        end_line = getattr(fn, "end_lineno", fn.body[-1].lineno)
        indent = " " * (fn.body[0].col_offset or 4)
        lines.insert(end_line, f"{indent}# contract-stable-equivalent\n")
        return "".join(lines)

    if kind == "CHANGE_RETURN_TYPE":
        # change `return <expr>` -> `return {'_ret': <expr>}` if a return exists
        for stmt in fn.body:
            if isinstance(stmt, ast.Return) and stmt.value is not None:
                start = stmt.lineno - 1
                line = lines[start]
                if "return " in line:
                    idx = line.index("return ") + len("return ")
                    expr = line[idx:].rstrip("\n")
                    lines[start] = line[:idx] + "{'_ret': " + expr + "}\n"
                    return "".join(lines)
        # no return: add one returning a dict-shaped value
        ins = fn.body[0].lineno - 1
        lines.insert(ins, "        return {'_ret': None}\n")
        return "".join(lines)

    if kind in ("ADD_REQUIRED_PARAM", "ADD_OPTIONAL_PARAM"):
        marker = ", *, req_marker_" if kind == "ADD_REQUIRED_PARAM" else ", opt_marker_=None"
        # locate the arglist's matching close-paren across the signature header
        header_end = fn.body[0].lineno - 1   # exclusive: lines up to first body stmt
        header = "".join(lines[fn.lineno - 1:header_end])
        # find the arglist open paren after `def <name>`
        def_idx = header.find("def ")
        open_idx = header.find("(", def_idx) if def_idx >= 0 else header.find("(")
        if open_idx < 0:
            raise AssertionError(f"no arglist paren in header: {header[:60]!r}")
        depth = 0
        close_idx = None
        for i in range(open_idx, len(header)):
            if header[i] == "(":
                depth += 1
            elif header[i] == ")":
                depth -= 1
                if depth == 0:
                    close_idx = i
                    break
        if close_idx is None:
            raise AssertionError("could not find arglist close paren")
        new_header = header[:close_idx] + marker + header[close_idx:]
        lines[fn.lineno - 1:header_end] = [new_header]
        return "".join(lines)

    if kind == "REMOVE_PARAM":
        args = [a.arg for a in fn.args.args]
        skip = 1 if (owner is not None and args and args[0] == "self") else 0
        removable = [a for a in args[skip:] if a != "self"]
        if not removable:
            raise AssertionError("no removable param")
        target = removable[-1]
        import re
        ann = r"(?:\s*:\s*[^,)=]+)?"
        patterns = [
            re.compile(r",\s*" + re.escape(target) + ann),            # comma-prefixed (middle/last)
            re.compile(r"\(\s*" + re.escape(target) + ann + r",\s*"), # first, has following comma
            re.compile(r"\(\s*" + re.escape(target) + ann + r"\s*\)"),# sole/last-no-comma
        ]
        header_end = fn.body[0].lineno - 1
        for i in range(fn.lineno - 1, header_end):
            for pat in patterns:
                m = pat.search(lines[i])
                if m:
                    lines[i] = lines[i][:m.start()] + lines[i][m.end():]
                    return "".join(lines)
        raise AssertionError(f"could not remove param {target} textually")

    raise ValueError(f"unknown kind {kind}")


def change_type_for(kind: str) -> ChangeType:
    return KIND_TO_CHANGE[kind]