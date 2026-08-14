"""Line-level coverage helper for v4.

Runs coverage for a single test file on a PRISTINE temp copy and returns
{source_file: set(executed_line_numbers)}. This lets us say a test "covers"
contract c iff it executes ≥1 line within c's [lineno, end_lineno] range —
much more precise than file-level (import-chain) coverage, and the key to
meaningful coverage-gap detection.

PRISTINE tree, INDEPENDENT of any change outcome (no oracle leakage).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import coverage as coverage_mod

import sys
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent.parent / "v3"))   # v3
from engine import REPOS_CFG, REPOS_DIR

_CACHE: dict = {}


def coverage_lines(repo: str, testfile: str) -> dict:
    """Return {source_file_rel: set(lines)} executed by `testfile` on pristine tree."""
    key = (repo, testfile)
    if key in _CACHE:
        return _CACHE[key]
    tmp = Path(tempfile.mkdtemp(prefix="v4cov-"))
    try:
        rcopy = tmp / repo
        shutil.copytree(REPOS_DIR / repo, rcopy)
        cov_file = rcopy / ".v4.coverage"
        env = {"PYTHONDONTWRITEBYTECODE": "1"}
        ignore = REPOS_CFG[repo]["test_ignore"]
        cmd = ["python3", "-m", "coverage", "run", "--source", repo,
               "--data-file", cov_file.name,
               "-m", "pytest", "-q", "--no-header", "-o", "addopts=",
               "-p", "no:cacheprovider", "-p", "no:cov", "--tb=short", testfile]
        cmd += ["--ignore=" + ig for ig in ignore]
        subprocess.run(cmd, cwd=str(rcopy), capture_output=True, text=True,
                       timeout=180, env=env)
        out: dict = {}
        if cov_file.exists():
            data = coverage_mod.CoverageData(basename=str(cov_file))
            try:
                data.read()
            except Exception:
                pass
            marker = f"/{repo}/"
            for f in data.measured_files():
                idx = f.rfind(marker)
                if idx < 0:
                    continue
                rel = f[idx + 1:]
                lines = data.lines(f)
                if lines:
                    out[rel] = set(lines)
        _CACHE[key] = out
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def covers_contract(line_map: dict, repo: str, contract) -> bool:
    """True iff some executed line falls within the contract's source body."""
    rng = symbol_range(repo, contract.artifact_id, contract.symbol)
    if rng is None:
        # fall back: contract must at least be in a file the test executed
        return contract.artifact_id in line_map
    start, end = rng
    executed = line_map.get(contract.artifact_id, set())
    # require a line STRICTLY INSIDE the body (start+1 .. end). The `def` line
    # itself (== start) executes at import time for every defined function, so
    # counting it would mark all imported symbols "covered" trivially. Only a
    # line in the body indicates the function was actually CALLED.
    return any((start + 1) <= ln <= end for ln in executed)


import ast as _ast
_RANGE_CACHE: dict = {}


def symbol_range(repo: str, artifact: str, symbol: str):
    """Return (start_line, end_line) of the dotted symbol in `artifact` (cached).
    `artifact` is repo-relative (e.g. 'tinydb/table.py'); file lives at
    REPOS_DIR/repo/artifact."""
    key = (repo, artifact, symbol)
    if key in _RANGE_CACHE:
        return _RANGE_CACHE[key]
    src_path = REPOS_DIR / repo / artifact
    if not src_path.exists():
        _RANGE_CACHE[key] = None
        return None
    try:
        tree = _ast.parse(src_path.read_text(encoding="utf-8"))
    except Exception:
        _RANGE_CACHE[key] = None
        return None
    parts = symbol.split(".")
    rng = None
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                and node.name == parts[0] and len(parts) == 1:
            rng = (node.lineno, getattr(node, "end_lineno", node.lineno))
            break
        if isinstance(node, _ast.ClassDef) and node.name == parts[0] and len(parts) == 2:
            for sub in node.body:
                if isinstance(sub, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                        and sub.name == parts[-1]:
                    rng = (sub.lineno, getattr(sub, "end_lineno", sub.lineno))
                    break
            if rng:
                break
    _RANGE_CACHE[key] = rng
    return rng