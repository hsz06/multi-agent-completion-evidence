"""Ground Truth dependency graph G* for each repo.

Built from:
  1. STATIC  : ast import edges  (FILE->FILE)
  2. DYNAMIC : coverage-measured test->code edges (CODE->TEST)
  3. MANUAL  : the key cross-agent edges ARTIFACT->COMPLETION and TASK->TASK
               that the change cases actually exercise. These are the edges
               the candidate generator must recover in Phase 2B.

The MANUAL edges are derived from the calibrated oracle: a producer file ->
{agent_b_completion, testing_completion} edge exists when a breaking change
to that producer made the corresponding verify-set FAIL at calibration time.
This is *programmatic* ground truth (oracle-driven), not hand-waving.
"""
from __future__ import annotations

import json
from pathlib import Path

from change_cases import CASES, REPO_VERIFY_SETS, CLAIM_SLOTS
from common.extract_static import StaticDependencyExtractor
from common.models import Dependency, RelType, Provenance, ChangeType
from common.classifier import ChangeClassifier
from common.repo_driver import RepoDriver


# Producer -> which downstream completion slot it actually feeds, per repo.
# Derived by reading the calibrated oracle: a slot depends on producer P if
# at least one change to P (in this repo) made that slot's verify-set FAIL.
def _manual_artifact_completion_edges(repo: str, oracle: dict) -> list[Dependency]:
    edges = []
    seen = set()
    for case in oracle["cases"]:
        if case.get("repo") != repo or not case.get("results") or case.get("applied") is False:
            continue
        producer = case["producer"]
        for slot, targets in [("agent_b_completion", "agent_b"),
                              ("testing_completion", "testing")]:
            res = case["results"].get(targets)
            if res and res["result"] == "FAIL":
                k = (producer, slot)
                if k in seen:
                    continue
                seen.add(k)
                edges.append(Dependency(
                    source=producer, target=slot,
                    relation_type=RelType.ARTIFACT_TO_COMPLETION,
                    scope=frozenset({ChangeType.BREAKING,
                                     ChangeType.POTENTIALLY_BREAKING}),
                    confidence=1.0, provenance=Provenance.MANUAL,
                    note=f"oracle: {case['case']} broke {targets} verify-set",
                ))
    return edges


# Static FILE->FILE gives MODULE->MODULE context for the candidate generator.
def _static_edges(repo: str) -> list[Dependency]:
    pkg = repo
    ext = StaticDependencyExtractor(Path(f"repos/{repo}"), pkg)
    return [
        Dependency(source=e.source, target=e.target,
                   relation_type=RelType.FILE_TO_FILE,
                   scope=frozenset({ChangeType.BREAKING,
                                    ChangeType.POTENTIALLY_BREAKING}),
                   confidence=e.confidence,
                   provenance=Provenance.STATIC, note=e.note)
        for e in ext.extract()
    ]


def build_ground_truth(repo: str, oracle: dict) -> list[Dependency]:
    edges = _static_edges(repo) + _manual_artifact_completion_edges(repo, oracle)
    # de-dup by (source,target,relation_type)
    seen, out = set(), []
    for e in edges:
        k = e.key()
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def dump_all():
    outdir = Path("ground_truth")
    outdir.mkdir(exist_ok=True)
    summary = {}
    for label, fn in [("base", "oracle_calibrated.json"),
                      ("extended", "oracle_calibrated_extended.json")]:
        oracle = json.load(open(fn))
        sub = outdir / label
        sub.mkdir(exist_ok=True)
        for repo in ("tinydb", "cerberus", "boltons"):
            (sub / repo).mkdir(parents=True, exist_ok=True)
            edges = build_ground_truth(repo, oracle)
            summary.setdefault(label, {})[repo] = len(edges)
            with open(sub / f"{repo}/dependencies.json", "w", encoding="utf-8") as f:
                json.dump({"repo": repo, "config": label,
                           "edges": [e.to_dict() for e in edges]},
                           f, indent=2)
    print("ground truth edge counts:", summary)


if __name__ == "__main__":
    dump_all()