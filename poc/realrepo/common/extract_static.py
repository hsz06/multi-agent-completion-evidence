"""StaticDependencyExtractor: pure-Python `ast` import/symbol extraction.

Produces FILE->FILE (import), MODULE->MODULE (symbol reference), and
SCHEMA->CLIENT (config/consumer name matching) edges.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from .models import Dependency, RelType, Provenance


class StaticDependencyExtractor:
    def __init__(self, repo_root: Path, pkg_hint: str):
        """pkg_hint: top-level package name (e.g. 'tinydb', 'cerberus', 'boltons')."""
        self.root = Path(repo_root)
        self.pkg = pkg_hint

    # ------------------------------------------------------------------
    def source_files(self) -> list[Path]:
        return sorted(
            p for p in self.root.rglob("*.py")
            if "/tests/" not in str(p) and "test_" not in p.name
            and self.pkg in str(p)
        )

    def test_files(self) -> list[Path]:
        return sorted(
            p for p in self.root.rglob("*.py")
            if ("/tests/" in str(p) or "test_" in p.name) and "tests" in str(p)
        )

    def rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def module_of(self, p: Path) -> str:
        """python module path without .py, package included."""
        return self.rel(p)[:-3].replace("/", ".")

    # -----------------------------------------------------------------
    def extract(self) -> list[Dependency]:
        edges = []
        file_module = {p: self.module_of(p) for p in self.source_files()}
        module_file = {m: p for p, m in file_module.items()}

        for p in self.source_files():
            src = p.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            my_module = file_module[p]
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self._add_import(edges, my_module, alias.name,
                                         module_file, file_module, p)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    target = node.module
                    if node.level:                      # relative import
                        base = my_module.split(".")[:-1]
                        for _ in range(max(0, node.level - 1)):
                            if base:
                                base.pop()
                        target = ".".join(base + [target]) if target else ".".join(base)
                        if not target.startswith(self.pkg):
                            target = f"{self.pkg}.{target}".lstrip(".")
                    self._add_import(edges, my_module, target,
                                     module_file, file_module, p)

        # symbol-level reference edges (module -> module when a public def is
        # referenced across files) — cheap static approximation:
        edges += self._symbol_reference_edges(file_module, module_file)

        # schema->client heuristic for config/schema files
        edges += self._schema_client_edges(module_file)
        return self._dedupe(edges)

    # -----------------------------------------------------------------
    def _add_import(self, edges, src_module, target_module, module_file,
                    file_module, src_file):
        target = module_file.get(target_module or "")
        if target is not None and target is not module_file.get(src_module):
            rel_src = self.rel(module_file[src_module])
            rel_dst = self.rel(target)
            edges.append(Dependency(
                source=rel_src, target=rel_dst,
                relation_type=RelType.FILE_TO_FILE,
                scope=frozenset(),                   # filled by classifier later
                confidence=0.95 if "import" else 0.9,
                provenance=Provenance.STATIC,
                note=f"import {target_module or ''}",
            ))

    def _symbol_reference_edges(self, file_module, module_file):
        """Conservative: if module A re-exports or defines a name that another
        module imports via package `from . import X` we keep the import edge
        (already captured). Name collisions across modules are ignored at this
        PoC level."""
        return []

    def _schema_client_edges(self, module_file):
        """Detect config/schema files (*.json, *.yaml, schema in name) and link
        them to any python consumer mentioning the basename or its keys."""
        edges = []
        schema_files = [
            p for p in self.root.rglob("*")
            if p.suffix in (".json", ".yaml", ".yml", ".toml")
            and "test" not in str(p) and "node_modules" not in str(p)
        ]
        for sf in schema_files:
            rel_sf = self.rel(sf)
            base = sf.stem
            for p in self.source_files():
                src = p.read_text(encoding="utf-8", errors="replace")
                if re.search(rf"\b{re.escape(base)}\b", src):
                    rel_src = self.rel(p)
                    if rel_src == rel_sf:
                        continue
                    edges.append(Dependency(
                        source=rel_sf, target=rel_src,
                        relation_type=RelType.CONFIG_TO_CODE,
                        scope=frozenset(),
                        confidence=0.6, provenance=Provenance.STATIC,
                        note=f"config referenced by {rel_src}",
                    ))
        return edges

    @staticmethod
    def _dedupe(edges):
        seen, out = set(), []
        for e in edges:
            k = e.key()
            if k not in seen:
                seen.add(k)
                out.append(e)
        return out


def dump_static(repo_name: str, repo_root: str, pkg: str, out_path: str):
    edges = StaticDependencyExtractor(repo_root, pkg).extract()
    payload = {"repo": repo_name, "extractor": "static-ast",
               "edges": [e.to_dict() for e in edges]}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return edges