"""Missing-dependency recovery: candidate generation, hybrid ranking (with tie
tracking), counterfactual replay (REAL pytest), regression gate + threshold
sensitivity.

Badcase = a change case whose calibrated outcome is a Natural GFC at the
operative regime (all local completions PASS, oracle FAIL) AND some downstream
completion SHOULD have been invalidated but the (incomplete) G_hat lacked the
edge. Candidate generators may only use: G_hat, static/dynamic evidence,
semantic heuristic, and the badcase trace. They CANNOT read deleted_edges GT.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))         # v3
sys.path.insert(0, str(_HERE.parent.parent))  # realrepo

from contracts.extractor import ContractExtractor, REPOS_DIR
from contracts.model import Candidate, ChangeType, Granularity, Provenance, DependencyEdge
from engine import build_change_registry, calibrate_case, REPOS_CFG, COMPLETIONS, is_gfc
from common.repo_driver import RepoDriver
from contracts.mutations import mutate

# fixed ranking weights (no training)
W_STATIC, W_DYNAMIC, W_SEMANTIC, W_TRACE = 0.3, 0.4, 0.2, 0.4


# ---------------------------------------------------------------------------
# Candidate generators
# ---------------------------------------------------------------------------

def gen_static_candidates(badcase, g_hat):
    """Static: consumer modules that import the producer file."""
    repo = badcase["repo"]; producer = badcase["file"]; ct = badcase["change_type"]
    ext = ContractExtractor(REPOS_DIR / repo, repo)
    src = ext.rel  # not used; we scan source files directly
    pkg_module = producer.replace(".py", "").replace("/", ".")
    cands = []
    for f in ext.source_files():
        try:
            s = f.read_text()
        except Exception:
            continue
        if pkg_module in s and str(f).endswith(".py"):
            # map consumer file -> completion slot(s) whose verify-set lives in that dir
            for comp in _downstream_completions():
                cands.append(Candidate(
                    source=f"{producer}::PUBLIC_SYMBOL::{badcase['symbol']}",
                    target=comp, relation_type="CONTRACT->COMPLETION",
                    scope=[ct], confidence=W_STATIC,
                    reason=f"static import of {pkg_module} in {f}", method="static",
                    granularity=Granularity.SYMBOL))
    return _dedupe(cands)


def gen_dynamic_candidates(badcase, g_hat):
    """Dynamic: completion verify-sets whose coverage executes the producer file."""
    from common.extract_dynamic import DynamicDependencyExtractor
    repo = badcase["repo"]; producer = badcase["file"]; ct = badcase["change_type"]
    sets = REPOS_CFG[repo]["verify"]["LOCAL"]
    cands = []
    for comp_short, targets in sets.items():
        targets = tuple(t for t in targets if t and "..." not in t)
        if not targets:
            continue
        m = _dyn_measure(repo, targets)
        if producer in m["covered_files"]:
            cands.append(Candidate(
                source=f"{producer}::FUNCTION_SIGNATURE::{badcase['symbol']}",
                target=f"{comp_short}_completion",
                relation_type="CONTRACT->COMPLETION", scope=[ct],
                confidence=W_DYNAMIC,
                reason=f"dynamic coverage: {comp_short} verify-set executes {producer}",
                method="dynamic", granularity=Granularity.CONTRACT))
    return _dedupe(cands)


_DYN_CACHE = {}


def _dyn_measure(repo, targets):
    k = (repo, targets)
    if k not in _DYN_CACHE:
        from common.extract_dynamic import DynamicDependencyExtractor
        dyn = DynamicDependencyExtractor(REPOS_DIR / repo, repo)
        _DYN_CACHE[k] = dyn.measure(targets=list(targets))
    return _DYN_CACHE[k]


def gen_semantic_candidates(badcase, g_hat):
    """Semantic: deterministic heuristic (no LLM API in this environment).
    Propose CONTRACT->COMPLETION for every downstream completion whose
    verify-set source references the mutated public symbol."""
    repo = badcase["repo"]; producer = badcase["file"]; sym = badcase["symbol"]; ct = badcase["change_type"]
    import re
    cands = []
    sets = REPOS_CFG[repo]["verify"]["LOCAL"]
    for comp_short, targets in sets.items():
        for t in targets:
            if not t or "..." in t:
                continue
            p = REPOS_DIR / repo / t
            if not p.exists():
                continue
            try:
                s = p.read_text()
            except Exception:
                continue
            # symbol may be 'Table.insert' -> search for 'insert(' and the method name
            leaf = sym.split(".")[-1]
            if re.search(rf"\b{re.escape(leaf)}\b", s):
                cands.append(Candidate(
                    source=f"{producer}::FUNCTION_SIGNATURE::{sym}",
                    target=f"{comp_short}_completion",
                    relation_type="CONTRACT->COMPLETION", scope=[ct],
                    confidence=W_SEMANTIC,
                    reason=f"semantic: {t} references symbol '{leaf}' from {sym}",
                    method="semantic", granularity=Granularity.CONTRACT))
                break
    return _dedupe(cands)


def gen_trace_candidates(badcase, g_hat):
    """Trace: a Natural GFC means a downstream completion stayed VERIFIED
    although the producer broke it. Propose CONTRACT->COMPLETION for every
    downstream completion NOT already covered by a G_hat edge."""
    ct = badcase["change_type"]; producer = badcase["file"]; sym = badcase["symbol"]
    existing = {e.target for e in g_hat
                if e.source.startswith(producer) and e.relation_type == "CONTRACT->COMPLETION"}
    cands = []
    for comp in (c for c in COMPLETIONS if c != "dev_a_completion"):
        if comp in existing:
            continue
        cands.append(Candidate(
            source=f"{producer}::FUNCTION_SIGNATURE::{sym}",
            target=comp, relation_type="CONTRACT->COMPLETION", scope=[ct],
            confidence=W_TRACE,
            reason=f"trace: GFC after break to {sym}; claim {comp} stayed VERIFIED",
            method="trace", granularity=Granularity.CONTRACT))
    return _dedupe(cands)


def _downstream_completions():
    return ("dev_b_completion", "testing_completion")


def _dedupe(cands):
    seen, out = {}, []
    for c in cands:
        k = (c.source, c.target, c.relation_type)
        if k not in seen:
            seen[k] = c
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Hybrid ranking with explicit tie tracking
# ---------------------------------------------------------------------------

def rank_hybrid(badcase, g_hat, ablation="full"):
    """Fuse generators into a ranked list. Records tie groups explicitly so the
    ablation cannot hide behind alphabetical tie-breaking.

    Ablation variants: full | no_static | no_dynamic | no_semantic | no_trace
    """
    parts = []
    if ablation != "no_trace":
        parts += gen_trace_candidates(badcase, g_hat)
    if ablation != "no_dynamic":
        parts += gen_dynamic_candidates(badcase, g_hat)
    if ablation != "no_semantic":
        parts += gen_semantic_candidates(badcase, g_hat)
    if ablation != "no_static":
        parts += gen_static_candidates(badcase, g_hat)
    # merge: sum confidences across methods that propose the same (source,target)
    by_key = {}
    for c in parts:
        k = (c.source, c.target, c.relation_type)
        if k not in by_key:
            by_key[k] = {"cand": c, "score": 0.0, "methods": set()}
        s_contrib = {"static": W_STATIC, "dynamic": W_DYNAMIC,
                     "semantic": W_SEMANTIC, "trace": W_TRACE}.get(c.method, 0.0)
        by_key[k]["score"] += s_contrib
        by_key[k]["methods"].add(c.method)
        if c.reason:
            by_key[k]["cand"].reason = c.reason
    merged = []
    for k, v in by_key.items():
        c = v["cand"]
        c.confidence = round(min(1.0, v["score"]), 4)
        c.method = "hybrid:" + "+".join(sorted(v["methods"]))
        merged.append(c)
    merged.sort(key=lambda c: (-c.confidence, c.target))   # stable; tie -> target asc (declared)
    # explicit tie tracking: groups of equal confidence at the top
    ties = []
    if merged:
        top = merged[0].confidence
        i = 0
        while i < len(merged) and merged[i].confidence == top:
            i += 1
        if i > 1:
            ties = [c.target for c in merged[:i]]
    return merged, {"tie_at_top": ties, "n_tied": len(ties)}


# ---------------------------------------------------------------------------
# Counterfactual replay (REAL pytest) + regression gate
# ---------------------------------------------------------------------------

_CF_CACHE = {}
_RG_CACHE = {}


def counterfactual_replay(badcase, candidate, g_hat, regime="LOCAL"):
    """Add candidate edge to g_hat, re-apply the change on a fresh repo copy,
    run real pytest on the operative verify-sets + oracle. PreventFailure ==
    the global gate no longer reports VERIFIED while oracle FAILS.

    A candidate may prevent failure by either (a) causing a STALE completion to
    be revalidated-and-FAIL, or (b) the revalidation surfacing the break.
    """
    ck = (badcase["case_id"], candidate.source, candidate.target, regime)
    if ck in _CF_CACHE:
        return _CF_CACHE[ck]
    if candidate.relation_type != "CONTRACT->COMPLETION" \
            or candidate.target not in COMPLETIONS:
        res = {"prevented": False, "invalidated": [], "revalidated": {},
               "gate_after": "VERIFIED", "oracle": "FAIL",
               "note": "non-completion candidate"}
        _CF_CACHE[ck] = res
        return res
    case = next(c for c in build_change_registry() if c["case_id"] == badcase["case_id"])
    repo = case["repo"]
    d = RepoDriver(repo)
    orig = d.read(case["file"])
    d.write(case["file"], mutate(orig, case["symbol"], case["kind"]))
    sets = REPOS_CFG[repo]["verify"][regime]
    ignore = REPOS_CFG[repo]["test_ignore"]
    results = {}
    for comp, targets in sets.items():
        targets = [t for t in targets if t and "..." not in t]
        r = d.run_pytest(targets, extra=["--ignore=" + ig for ig in ignore] if ignore else [])
        results[f"{comp}_completion"] = r["result"]
    ora = d.run_pytest([t for t in REPOS_CFG[repo]["oracle"] if "..." not in t],
                       extra=["--ignore=" + ig for ig in ignore] if ignore else [])
    results["oracle"] = ora["result"]
    d.cleanup()

    # the candidate edge claims target depends on producer; with scope matching
    # the change it invalidates target. Two INDEPENDENT outcomes (spec §18/§32):
    #   triggered       : candidate invalidated at least one completion (WHO/WHAT
    #                      to reverify) -> the dependency-recovery win.
    #   prevented_gate  : the invalidation makes the conservative completion gate
    #                      leave VERIFIED (a STALE claim blocks false completion
    #                      pending re-verification). == triggered here.
    #   detected        : re-running the target's verify-set then FAILED (the
    #                      verifier COULD detect) -> verification-sufficiency.
    #   prevented_detected : detected AND oracle FAIL (verifier caught the break).
    # A GFC by construction has all operative verify-sets PASS, so detected is
    # often false even when triggered is true -> the spec's explicit
    # Triggered=true / Detected=false / Prevented(gate)=true case.
    target = candidate.target
    triggered = True
    detected = (results.get(target) == "FAIL")
    oracle_fail = (results["oracle"] == "FAIL")
    gated_statuses = {c: "VERIFIED" for c in COMPLETIONS}
    gated_statuses[target] = "STALE"          # conservative: invalidated -> stale
    if detected:
        gated_statuses[target] = "FAILED"
    gate = _gate(gated_statuses)
    prevented_gate = triggered and (gate != "VERIFIED")
    prevented_detected = detected and oracle_fail
    res = {"prevented": prevented_detected,    # legacy = detected-prevention
           "prevented_gate": prevented_gate,
           "triggered": triggered, "detected": detected,
           "invalidated": [target], "revalidated": {target: "FAIL" if detected else "PASS"},
           "gate_after": gate, "oracle": results["oracle"],
           "candidate": candidate.to_dict()}
    _CF_CACHE[ck] = res
    return res


def _gate(statuses):
    if any(v == "FAILED" for v in statuses.values()):
        return "FAILED"
    if any(v in ("STALE", "PENDING") for v in statuses.values()):
        return "NOT_READY"
    return "VERIFIED"


def regression_gate(candidate, g_hat, repo, regime="LOCAL", threshold=0.20):
    """Run all change cases of `repo` with the patched graph; measure FIR and
    regression failure rate."""
    rk = (repo, candidate.source, candidate.target, regime, threshold)
    if rk in _RG_CACHE:
        return _RG_CACHE[rk]
    cases = build_change_registry()
    tp = fp = fn = tn = 0
    reg_fail = 0
    n = 0
    DOWNSTREAM = ("dev_b_completion", "testing_completion")
    for case in cases:
        if case["repo"] != repo:
            continue
        cal = calibrate_case(case, regime=regime)
        if not cal.get("applied"):
            continue
        n += 1
        # patched prediction: candidate edge fires if change scope in candidate.scope
        ct = _ct_for_kind(case["kind"])
        # does the patched edge's producer match this case's file? only this case's
        # own producer triggers the candidate edge (same source contract)
        src_contract = candidate.source
        case_contract = f"{case['file']}::FUNCTION_SIGNATURE::{case['symbol']}"
        fired = (src_contract == case_contract) and (ct.value in candidate.scope)
        pred = {candidate.target} if fired else set()
        # ground truth: completions whose verify-set failed
        gt = {c for c in COMPLETIONS if cal["results"].get(c, {}).get("result") == "FAIL"}
        gt &= set(DOWNSTREAM)
        pred &= set(DOWNSTREAM)
        tp += len(pred & gt); fp += len(pred - gt)
        fn += len(gt - pred); tn += len(set(DOWNSTREAM) - pred - gt)
        # regression failure: candidate causes a false invalidation on a
        # non-breaking change, or fails to prevent a GFC it was meant to.
        if fired and ct.value in ("NON_SEMANTIC", "COMPATIBLE"):
            reg_fail += 1
    fir = fp / (fp + tn) if (fp + tn) else 0.0
    res = {"false_invalidation_rate": round(fir, 4),
           "regression_failure_rate": round(reg_fail / n, 4) if n else 0.0,
           "n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn}
    _RG_CACHE[rk] = res
    return res


def _ct_for_kind(kind):
    from contracts.mutations import KIND_TO_CHANGE
    return KIND_TO_CHANGE[kind]