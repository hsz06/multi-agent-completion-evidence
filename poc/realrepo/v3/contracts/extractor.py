"""Contract extractor: AST-based, Python-only (all v3 repos are Python).

Extracts ContractNode instances from repo source:
  FUNCTION_SIGNATURE  — public function/method signature (params)
  RETURN_CONTRACT     — annotated (or None-default) return contract
  PUBLIC_SYMBOL       — public class/module-level symbol existence
  SCHEMA_FIELD        — fields of dict-shaped schema literals / dataclasses
  CONFIG_KEY          — keys consumed from a config/settings module
  TYPE_CONTRACT       — public class type contract

TS/JS extraction is provided as an interface stub (not exercised — no JS repo).
Schema (OpenAPI/JSON Schema) extraction is best-effort via json scan.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from .model import ContractNode, ContractType


def _is_public(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _params(fn) -> list:
    a = fn.args
    defaults = [None] * (len(a.args) - len(a.defaults)) + list(a.defaults)
    out = []
    for arg, default in zip(a.args, defaults):
        out.append({"name": arg.arg, "kind": "optional" if default is not None else "required",
                    "ann": _ann(arg.annotation)})
    for p in a.posonlyargs:
        out.append({"name": p.arg, "kind": "posonly-required", "ann": _ann(p.annotation)})
    kw_defaults = [None] * (len(a.kwonlyargs) - len(a.kw_defaults)) + list(a.kw_defaults)
    for kw, default in zip(a.kwonlyargs, kw_defaults):
        out.append({"name": kw.arg, "kind": "kwonly-optional" if default is not None else "kwonly-required",
                    "ann": _ann(kw.annotation)})
    return out


def _ann(node):
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node)


class ContractExtractor:
    def __init__(self, repo_root: Path, pkg: str):
        self.root = Path(repo_root)
        self.pkg = pkg

    # --- discovery ---------------------------------------------------------
    def source_files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*.py")
                      if f"{self.pkg}/" in str(p) and "/tests/" not in str(p)
                      and "/test_" not in p.name and "__pycache__" not in str(p))

    def rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    # --- extraction --------------------------------------------------------
    def extract_file(self, p: Path) -> list[ContractNode]:
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return []
        artifact = self.rel(p)
        nodes = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
                self._func_contracts(nodes, artifact, node, src)
            elif isinstance(node, ast.ClassDef) and _is_public(node.name):
                nodes.append(ContractNode(
                    contract_id=f"{artifact}::PUBLIC_SYMBOL::{node.name}",
                    artifact_id=artifact, contract_type=ContractType.PUBLIC_SYMBOL,
                    symbol=node.name, signature={"type": "class"},
                    location=f"{artifact}:{node.lineno}"))
                nodes.append(ContractNode(
                    contract_id=f"{artifact}::TYPE_CONTRACT::{node.name}",
                    artifact_id=artifact, contract_type=ContractType.TYPE_CONTRACT,
                    symbol=node.name, signature={"bases": [_ann(b) for b in node.bases]},
                    location=f"{artifact}:{node.lineno}"))
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(sub.name):
                        self._func_contracts(nodes, artifact, sub, src, owner=node.name)
        # best-effort: config keys (module-level UPPER = const config) and schema-ish dicts
        nodes += self._config_keys(tree, artifact, src)
        return nodes

    def _func_contracts(self, nodes, artifact, fn, src, owner=None):
        name = f"{owner}.{fn.name}" if owner else fn.name
        params = _params(fn)
        ret = _ann(fn.returns)
        nodes.append(ContractNode(
            contract_id=f"{artifact}::FUNCTION_SIGNATURE::{name}",
            artifact_id=artifact, contract_type=ContractType.FUNCTION_SIGNATURE,
            symbol=name, signature={"params": params, "return": ret},
            location=f"{artifact}:{fn.lineno}"))
        if ret is not None:
            nodes.append(ContractNode(
                contract_id=f"{artifact}::RETURN_CONTRACT::{name}",
                artifact_id=artifact, contract_type=ContractType.RETURN_CONTRACT,
                symbol=f"{name}::return", signature={"return": ret},
                location=f"{artifact}:{fn.lineno}"))

    def _config_keys(self, tree, artifact, src):
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    nm = getattr(tgt, "id", None)
                    if nm and nm.isupper() and len(nm) > 1:
                        try:
                            val = ast.unparse(node.value)
                        except Exception:
                            val = "?"
                        nodes.append(ContractNode(
                            contract_id=f"{artifact}::CONFIG_KEY::{nm}",
                            artifact_id=artifact, contract_type=ContractType.CONFIG_KEY,
                            symbol=nm, signature={"default": val[:60]},
                            location=f"{artifact}:{node.lineno}"))
        return nodes

    def extract(self) -> list[ContractNode]:
        out = []
        for p in self.source_files():
            out.extend(self.extract_file(p))
        return out

    # --- schema (OpenAPI / JSON Schema) best-effort -----------------------
    def extract_schema_fields(self) -> list[ContractNode]:
        out = []
        for p in sorted(self.root.rglob("*")):
            if p.suffix not in (".json",) or "test" in str(p) or "node_modules" in str(p):
                continue
            try:
                data = json.loads(p.read_text())
            except Exception:
                continue
            artifact = self.rel(p)
            fields = self._walk_schema(data, artifact)
            out.extend(fields)
        return out

    def _walk_schema(self, obj, artifact, prefix=""):
        out = []
        if isinstance(obj, dict):
            props = obj.get("properties") or obj.get("fields") or {}
            if isinstance(props, dict):
                for k, v in props.items():
                    t = v.get("type") if isinstance(v, dict) else None
                    req = k in (obj.get("required") or [])
                    out.append(ContractNode(
                        contract_id=f"{artifact}::SCHEMA_FIELD::{prefix}{k}",
                        artifact_id=artifact, contract_type=ContractType.SCHEMA_FIELD,
                        symbol=f"{prefix}{k}",
                        signature={"type": t, "required": req},
                        location=artifact))
        return out


def extract_repo(repo_root: str, pkg: str) -> list[ContractNode]:
    return ContractExtractor(Path(repo_root), pkg).extract()


REPOS_DIR = Path(__file__).resolve().parents[2] / "repos"


if __name__ == "__main__":
    for repo, pkg in [("tinydb", "tinydb"), ("toolz", "toolz"),
                      ("pyparsing", "pyparsing"), ("cerberus", "cerberus"),
                      ("boltons", "boltons")]:
        nodes = extract_repo(REPOS_DIR / repo, pkg)
        from collections import Counter
        c = Counter(n.contract_type.value for n in nodes)
        print(f"{repo}: {len(nodes)} contracts — {dict(c)}")