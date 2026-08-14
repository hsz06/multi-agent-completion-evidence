"""Build the VerificationPool: per-repo test-file obligations with PRISTINE-tree
coverage mapping (obligation -> covered source files / contracts / symbols).

Coverage is measured on the UNCHANGED tree, independent of any change outcome.
This is the key anti-leakage property: selection uses only this pre-change
coverage, never the held-out per_file PASS/FAIL matrix.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))          # realrepo
sys.path.insert(0, str(_HERE.parent.parent / "v3"))   # v3
sys.path.insert(0, str(_HERE.parent))                 # v4

from common.extract_dynamic import DynamicDependencyExtractor
from common.repo_driver import RepoDriver
from engine import REPOS_CFG, REPOS_DIR
from contracts.extractor import ContractExtractor
from contracts.model import ContractNode, ContractType
from config import POOL_FILES
from obligation.model import (VerificationObligation, VerifierType,
                              ObligationSource, CoverageGap)
from obligation.line_cov import coverage_lines, covers_contract

POOL_CACHE = {}


def _contracts_for_repo(repo: str) -> dict:
    """contract_id -> ContractNode (pristine extraction)."""
    ext = ContractExtractor(REPOS_DIR / repo, repo)
    return {c.contract_id: c for c in ext.extract()}


def build_pool(repo: str) -> list[VerificationObligation]:
    """Build obligations for `repo` with LINE-LEVEL pre-change coverage.
    A test covers a contract iff it executes ≥1 line within the symbol's
    source range (not merely imports the file). Cached per process."""
    if repo in POOL_CACHE:
        return POOL_CACHE[repo]
    contracts = _contracts_for_repo(repo)
    baseline = _load_baseline(repo)
    obligations = []
    for f in POOL_FILES[repo]:
        line_map = coverage_lines(repo, f)
        covered_src = sorted(line_map.keys())
        cc, cs = set(), set()
        for cid, c in contracts.items():
            if c.contract_type != ContractType.FUNCTION_SIGNATURE:
                continue
            if covers_contract(line_map, repo, c):
                cc.add(cid)
                cs.add(c.symbol)
        cost = baseline.get(f, {}).get("duration_s", 0.5)
        obligations.append(VerificationObligation(
            obligation_id=f"{repo}::{f}",
            repo=repo,
            verifier_type=_classify(f, set(covered_src)),
            command=f"pytest {f}",
            target_tests=f,
            covered_contracts=sorted(cc),
            covered_symbols=sorted(cs),
            covered_files=covered_src,
            estimated_cost=cost,
            source=ObligationSource.DYNAMIC,
        ))
    POOL_CACHE[repo] = obligations
    return obligations


def _classify(testfile: str, covered_src: set) -> VerifierType:
    """Heuristic verifier_type: a test covering >1 source module -> MODULE/INTEGRATION."""
    n = len([s for s in covered_src if s.endswith(".py")])
    if n >= 3:
        return VerifierType.INTEGRATION_TEST
    if n == 2:
        return VerifierType.MODULE_TEST
    return VerifierType.UNIT_TEST


def _load_baseline(repo: str) -> dict:
    p = _HERE.parent / "evaluation_private_oracle" / "pool_baseline.json"
    if not p.exists():
        return {}
    return json.load(open(p)).get(repo, {})


def dump_pools():
    outdir = _HERE.parent / "agent_visible_verification_pool"
    outdir.mkdir(exist_ok=True)
    summary = {}
    for repo in POOL_FILES:
        obs = build_pool(repo)
        with open(outdir / f"{repo}.json", "w") as fp:
            json.dump({"repo": repo, "obligations": [o.to_dict() for o in obs]}, fp, indent=2)
        summary[repo] = len(obs)
    return summary


if __name__ == "__main__":
    s = dump_pools()
    print("pool sizes:", s)
    for repo in POOL_FILES:
        obs = build_pool(repo)
        sample = obs[0]
        print(f"  {repo} e.g. {sample.target_tests}: cost={sample.estimated_cost}s, "
              f"covers {len(sample.covered_contracts)} contracts / "
              f"{len(sample.covered_files)} files")