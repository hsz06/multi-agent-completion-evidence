"""Phase 2B: missing dependency recovery.

Pipeline:
  G*  --delete p% of ARTIFACT->COMPLETION edges (seeds 42/43/44)-->  G_hat
  run change cases on G_hat -> collect badcases (GFC == TRUE)
  for each badcase: 4 candidate generators -> rank Top-K
  counterfactual replay each candidate (REAL pytest) -> PreventFailure?
  regression gate on all change cases of that repo
  metrics: Recall@1/3/5, Precision@K, MRR, FixRate, AcceptRate, RegFailRate, FIR
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from change_cases import CASES, REPO_VERIFY_SETS
from common.models import Dependency, RelType, ChangeType, Provenance, Candidate
from common.classifier import ChangeClassifier
from common.repo_driver import RepoDriver
from experiment_engine import (
    make_world, replay_case, ground_truth_invalidation,
    load_oracle, find_oracle_case, verify_sets_for, GLOBAL_OK,
)
from common.extract_static import StaticDependencyExtractor
from common.extract_dynamic import DynamicDependencyExtractor

# Phase 2B uses the EXTENDED configuration so that breaking changes are
# observable by the testing claim's verify-set, making missing-edge GFCs
# recoverable via counterfactual replay. The BASE config is retained as the
# "unrecoverable when oracle-only" control arm (see coverage sensitivity).
ORACLE = None  # lazily loaded extended oracle
_CF_CACHE: dict = {}   # (repo, case_id, src, tgt) -> counterfactual result
_RG_CACHE: dict = {}   # (repo, src, tgt, scope) -> regression gate result


def _sets(repo: str) -> dict:
    global ORACLE
    if ORACLE is None:
        ORACLE = load_oracle(extended=True)
    return verify_sets_for(ORACLE, repo)


# --------------------------------------------------------------------------
# Edge deletion
# --------------------------------------------------------------------------

def load_gstar(repo: str) -> list:
    d = json.load(open(f"ground_truth/extended/{repo}/dependencies.json"))
    return [Dependency.from_dict(e) for e in d["edges"]]


def delete_edges(gstar: list, ratio: float, seed: int) -> tuple[list, list]:
    """Delete `ratio` of ARTIFACT->COMPLETION edges (priority targets per spec).
    Returns (g_hat, deleted_edges)."""
    rng = random.Random(seed)
    targets = [e for e in gstar if e.relation_type == RelType.ARTIFACT_TO_COMPLETION]
    n_del = max(1, round(len(targets) * ratio)) if targets else 0
    n_del = min(n_del, len(targets))
    perm = sorted(targets, key=lambda e: rng.random())
    deleted = perm[:n_del]
    deleted_keys = {e.key() for e in deleted}
    g_hat = [e for e in gstar if e.key() not in deleted_keys]
    return g_hat, deleted


# --------------------------------------------------------------------------
# Badcase generation
# --------------------------------------------------------------------------

def make_badcases(g_hat: list, repo: str, oracle: dict, seed: int,
                  ratio: float) -> list:
    badcases = []
    for case in CASES:
        if case["repo"] != repo:
            continue
        oc = find_oracle_case(oracle, repo, case["case_id"])
        if oc.get("applied") is False:
            continue
        world = make_world(repo, g_hat, strategy="change_aware")
        trace = replay_case(world, case, oc)
        if trace["global_false_completion"]:
            badcases.append({
                "badcase_id": f"{repo}-{case['case_id']}-s{seed}-r{int(ratio*100)}",
                "repo": repo, "seed": seed, "ratio": ratio,
                "case_id": case["case_id"],
                "producer": case["producer"],
                "change_type": trace["change_type"],
                "claims_before": {k: "VERIFIED" for k in world.claims},
                "claims_after": trace["claims_after"],
                "global_after": trace["global_after"],
                "oracle_result": trace["oracle_result"],
                "current_graph": [e.to_dict() for e in g_hat],
                "deleted_edges_gt": None,  # filled by caller
                "world_snapshot": None,
            })
    return badcases


# --------------------------------------------------------------------------
# Candidate generators
# --------------------------------------------------------------------------

def _consumer_files(repo: str, producer_rel: str) -> list:
    """Candidate target FILES that might consume the producer. We return the
    set of repo source files that statically import the producer module, plus
    the producer itself's known downstream via G_hat."""
    pkg = repo
    ext = StaticDependencyExtractor(Path(f"repos/{repo}"), pkg)
    files = ext.source_files()
    consumers = []
    prod_module = producer_rel.replace(".py", "").replace("/", ".")
    for f in files:
        try:
            src = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if prod_module in src and str(f) != producer_rel:
            consumers.append((f, src))
    return consumers


def _existing_completion_targets(g_hat: list, producer: str) -> set:
    return {e.target for e in g_hat
            if e.source == producer and e.relation_type == RelType.ARTIFACT_TO_COMPLETION}


def gen_trace_heuristic(badcase: dict, g_hat: list) -> list[Candidate]:
    """Method 1: a Global False Completion means some downstream claim stayed
    VERIFIED although the producer broke. We propose producer->slot for every
    downstream completion slot that is NOT already covered by an edge in
    G_hat. Confidence is uniform; ranking is left to the fused method.

    Honest trace signal only: GFC + missing edge. It does NOT peek at which
    slot's verify-set failed — that is established later by counterfactual
    replay (the oracle).
    """
    producer = badcase["producer"]
    ct = badcase["change_type"]
    cands = []
    existing = _existing_completion_targets(g_hat, producer)
    for slot in ("agent_b_completion", "testing_completion"):
        if slot in existing:
            continue
        cands.append(Candidate(
            source=producer, target=slot,
            relation_type="ARTIFACT->COMPLETION", scope=[ct],
            confidence=0.55,
            reason=f"GFC occurred after break to {producer}; claim {slot} "
                   f"stayed VERIFIED — candidate missing edge",
            method="trace"))
    return cands


def gen_static_only(badcase: dict, g_hat: list) -> list[Candidate]:
    """Method 2: pure static — consumer files importing the producer."""
    producer = badcase["producer"]
    ct = badcase["change_type"]
    cands = []
    consumers = _consumer_files(badcase["repo"], producer)
    # map consumer files -> completion slot by current verify-set ownership:
    # we don't know file->claim mapping statically, so propose ARTIFACT->FILE
    # edges (still a valid recovery signal for downstream invalidation logic).
    for f, _ in consumers:
        cands.append(Candidate(
            source=producer, target=str(f),
            relation_type="FILE->FILE", scope=[ct],
            confidence=0.5,
            reason=f"{f} statically imports {producer}",
            method="static"))
    return cands


def gen_dynamic_only(badcase: dict, g_hat: list) -> list[Candidate]:
    """Method 3: pure dynamic — coverage shows producer executed by a test set
    that maps to a completion slot."""
    producer = badcase["producer"]
    ct = badcase["change_type"]
    repo = badcase["repo"]
    sets = _sets(repo)
    cands = []
    dyn = DynamicDependencyExtractor(Path(f"repos/{repo}"), repo)
    for slot, key in [("agent_b_completion", "agent_b"),
                      ("testing_completion", "testing")]:
        targets = sets.get(key, [])
        if not targets:
            continue
        m = dyn.measure(targets=targets)
        if producer in m["covered_files"]:
            existing = _existing_completion_targets(g_hat, producer)
            if slot in existing:
                continue
            cands.append(Candidate(
                source=producer, target=slot,
                relation_type="ARTIFACT->COMPLETION", scope=[ct],
                confidence=0.78,
                reason=f"{slot}'s verify-set dynamically executes {producer}",
                method="dynamic"))
    return cands


def gen_combined(badcase: dict, g_hat: list,
                 use_static=True, use_dynamic=True, use_semantic=True,
                 use_trace=True) -> list[Candidate]:
    """Method 4: weighted fusion. Returns ranked candidates (highest first)."""
    cands = []
    if use_trace:
        cands += gen_trace_heuristic(badcase, g_hat)
    if use_dynamic:
        cands += gen_dynamic_only(badcase, g_hat)
    if use_static:
        cands += gen_static_only(badcase, g_hat)
    if use_semantic:
        cands += gen_semantic(badcase, g_hat)
    # merge same (source,target,relation): keep max confidence, sum reasons
    by_key = {}
    for c in cands:
        k = (c.source, c.target, c.relation_type)
        if k not in by_key:
            by_key[k] = c
        else:
            base = by_key[k]
            # multi-source corroboration bumps confidence (each independent
            # signal that agrees adds evidence the edge is real)
            base.confidence = max(base.confidence, c.confidence) + 0.08
            base.reason = base.reason + " | " + c.reason
            base.method = "combined"
    ranked = sorted(by_key.values(),
                    key=lambda c: (-c.confidence, c.target))
    return ranked


def gen_semantic(badcase: dict, g_hat: list) -> list[Candidate]:
    """Semantic: producer's public symbols referenced by a completion's
    verify-set source."""
    from common.semantic import SemanticCandidateExtractor
    producer = badcase["producer"]
    ct = badcase["change_type"]
    repo = badcase["repo"]
    sem = SemanticCandidateExtractor(use_llm=False)
    try:
        prod_src = Path(f"repos/{repo}/{producer}").read_text()
    except Exception:
        return []
    # completion-slot verify-set sources
    sets = _sets(repo)
    cands = []
    for slot, key in [("agent_b_completion", "agent_b"),
                      ("testing_completion", "testing")]:
        files = []
        for t in sets.get(key, []):
            p = Path(f"repos/{repo}/{t}")
            if p.exists():
                files.append(p)
        if not files:
            continue
        produced = sem.extract_candidates(
            changed_producer=producer, changed_src=prod_src,
            consumer_files=files, change_type=ChangeType(ct))
        existing = _existing_completion_targets(g_hat, producer)
        for c in produced:
            if c.target in existing:
                continue
            # promote FILE candidates that target a verify-set file to the
            # corresponding completion slot
            tgt_slot = _file_to_slot(c.target, repo)
            if tgt_slot:
                cands.append(Candidate(
                    source=producer, target=tgt_slot,
                    relation_type="ARTIFACT->COMPLETION", scope=[ct],
                    confidence=c.confidence,
                    reason=f"semantic: {c.reason}", method="semantic"))
            else:
                cands.append(c)
    return cands


def _file_to_slot(file_rel: str, repo: str) -> str | None:
    sets = _sets(repo)
    for slot, key in [("agent_b_completion", "agent_b"),
                      ("testing_completion", "testing")]:
        for t in sets.get(key, []):
            if file_rel.endswith(t) or t.endswith(file_rel):
                return slot
    return None


# --------------------------------------------------------------------------
# Counterfactual replay — REAL pytest
# --------------------------------------------------------------------------

def counterfactual_replay(badcase: dict, candidate: Candidate,
                          g_hat: list) -> dict:
    """Add candidate edge to g_hat, re-apply the change on a fresh repo copy,
    run real pytest on ALL local verify-sets + oracle. PreventFailure == the
    global gate no longer says VERIFIED while oracle FAILS.

    Memoized on (repo, case_id, candidate source/target) since the real pytest
    result depends only on the changed tree (fixed) + candidate edge — not on
    which generator proposed it or the deletion seed.
    """
    cache_key = (badcase["repo"], badcase["case_id"],
                 candidate.source, candidate.target)
    if cache_key in _CF_CACHE:
        return _CF_CACHE[cache_key]
    case = next(c for c in CASES if c["case_id"] == badcase["case_id"]
                and c["repo"] == badcase["repo"])
    repo = badcase["repo"]
    # Only ARTIFACT->COMPLETION edges can change invalidation behaviour; other
    # candidate relation types are not applicable to gate prevention.
    if candidate.relation_type != "ARTIFACT->COMPLETION" \
            or candidate.target not in ("agent_b_completion", "testing_completion"):
        result = {"candidate": candidate.to_dict(), "invalidated": [],
                  "revalidated": {}, "global_after": "VERIFIED",
                  "oracle_result": "FAIL", "prevent_failure": False,
                  "note": "non-completion candidate: not applicable to gate"}
        _CF_CACHE[cache_key] = result
        return result
    d = RepoDriver(repo)
    orig = d.read(case["producer"])
    d.write(case["producer"], case["transform"](orig))
    sets = _sets(repo)
    results = {}
    for k, targets in sets.items():
        r = d.run_pytest(targets)
        results[k] = {"result": r["result"], "tests_failed": r["tests_failed"],
                      "duration_s": r["duration_s"]}
    d.cleanup()

    # build patched world and replay using the SAME real results
    patched = list(g_hat)
    patched.append(Dependency(
        source=candidate.source, target=candidate.target,
        relation_type=RelType.ARTIFACT_TO_COMPLETION,
        scope=frozenset(ChangeType(c) for c in candidate.scope),
        confidence=candidate.confidence,
        provenance=Provenance.SEMANTIC,
        note=candidate.reason,
    ))
    world = make_world(repo, patched, strategy="change_aware")
    oc = find_oracle_case(load_oracle(extended=True), repo, case["case_id"])
    # patch oracle_case results with the freshly-run ones for the patched graph
    oc_patched = dict(oc)
    oc_patched["results"] = results
    trace = replay_case(world, case, oc_patched)
    result = {
        "candidate": candidate.to_dict(),
        "invalidated": trace["invalidated"],
        "revalidated": trace["revalidated"],
        "global_after": trace["global_after"],
        "oracle_result": trace["oracle_result"],
        "prevent_failure": not trace["global_false_completion"],
    }
    _CF_CACHE[cache_key] = result
    return result


# --------------------------------------------------------------------------
# Regression gate
# --------------------------------------------------------------------------

def regression_gate(candidate: Candidate, g_hat: list, repo: str) -> dict:
    """Run ALL change cases of `repo` with the patched graph; measure FIR and
    regression failure rate (cases newly turning GFC or FAILED)."""
    rg_key = (repo, candidate.source, candidate.target,
              tuple(sorted(candidate.scope)))
    if rg_key in _RG_CACHE:
        return _RG_CACHE[rg_key]
    from experiment_engine import GLOBAL_OK
    oracle = load_oracle(extended=True)
    patched = list(g_hat) + [Dependency(
        source=candidate.source, target=candidate.target,
        relation_type=RelType.ARTIFACT_TO_COMPLETION,
        scope=frozenset(ChangeType(c) for c in candidate.scope),
        confidence=candidate.confidence, provenance=Provenance.SEMANTIC,
        note=candidate.reason)]
    DOWNSTREAM = ("agent_b_completion", "testing_completion")
    tp = fp = fn = tn = 0
    reg_fail = 0
    n = 0
    for case in CASES:
        if case["repo"] != repo:
            continue
        oc = find_oracle_case(oracle, repo, case["case_id"])
        if oc.get("applied") is False:
            continue
        n += 1
        world = make_world(repo, patched, strategy="change_aware")
        trace = replay_case(world, case, oc)
        gt = ground_truth_invalidation(case, oc)
        pred = set(trace["invalidated"])
        tp += len(pred & gt); fp += len(pred - gt)
        fn += len(gt - pred); tn += len(set(DOWNSTREAM) - pred - gt)
        # regression failure: a previously-OK case now reports GFC or a spurious
        # FAILED claim on a non-breaking change.
        if trace["global_false_completion"]:
            reg_fail += 1
        if oc["classified_ct"] in ("NON_SEMANTIC", "COMPATIBLE") and pred:
            reg_fail += 1
    fir = fp / (fp + tn) if fp + tn else 0.0
    result = {"false_invalidation_rate": round(fir, 4),
              "regression_failure_rate": round(reg_fail / n, 4) if n else 0.0,
              "n": n}
    _RG_CACHE[rg_key] = result
    return result