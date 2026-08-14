"""v4.2 HybridSemanticSelector: deterministic-first + LLM-fallback + EarlyStop.

should_invoke_llm(case) triggers LLM only when deterministic signal is
ambiguous/blind. LLM proposes semantic candidates from the SAME pool; we fuse
its relevance with deterministic signals (fixed weights, no held-out tuning).
Final pick: top1 (or top2) by fused score; detection judged by real pytest.

LLM NEVER reads the held-out oracle; this module never reads per_file.json
for ranking (only strategies._file_fails is used at evaluation time inside
the runner, not here).
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))

from obligation.pool import build_pool
from obligation.coverage import compute_gap, candidates_for_gap, covered_by_files
from assertion import file_sensitivity
from llm_semantic import (llm_available, build_prompt, call_llm, PROMPT_VERSION, MODEL)
from config import EXISTING_VERIFY_SET

CONFIDENCE_THRESHOLD = 0.5
LLM_INVOCATIONS = []   # log of {case_id, repo, prompt_variant, tokens, latency, n_candidates}


def should_invoke_llm(det_candidates, top1_sens, changed_is_private, indirect) -> bool:
    """Trigger LLM only on ambiguous/blind cases (spec §8)."""
    if not llm_available():
        return False
    if len(det_candidates) == 0:           # A: no deterministic candidate
        return True
    if top1_sens < CONFIDENCE_THRESHOLD:   # B: low confidence
        return True
    # C: top candidates tied (>=2 with same top score)
    if len(det_candidates) >= 2:
        s = sorted(det_candidates, key=lambda x: -x[1])
        if abs(s[0][1] - s[1][1]) < 1e-6:
            return True
    if changed_is_private:                 # D: private changed symbol, weak static path
        return True
    if indirect:                           # E: indirect helper/fixture pattern
        return True
    return False


def _candidate_pool_for_llm(repo, case_file, sym, kind, claim, existing):
    """Pool the LLM sees: candidates covering required contracts OR covering a
    private symbol of the changed file OR direct-callers. Bound to <=10."""
    g = compute_gap(repo, claim, case_file, sym)
    gap_cands = candidates_for_gap(repo, g, existing)
    gap_ids = {o.obligation_id for o in gap_cands}
    extras = []
    for o in build_pool(repo):
        if o.target_tests in existing or o.obligation_id in gap_ids:
            continue
        s, _ = file_sensitivity(repo, o.target_tests, sym, kind)
        if s > 0:   # direct-call
            extras.append(o)
    # also include a few "broad" pool files (cover the changed file at all)
    for o in build_pool(repo):
        if o.target_tests in existing or o.obligation_id in gap_ids:
            continue
        if case_file in o.covered_files and o not in extras:
            extras.append(o)
        if len(gap_cands) + len(extras) >= 10:
            break
    return (list(gap_cands) + extras)[:10]


def hybrid_select(repo, case_file, sym, kind, claim, existing,
                  w_assertion=0.35, w_coverage=0.25, w_llm=0.30,
                  top_k=1, prompt_variant="C"):
    """Return (files_to_run, llm_used, llm_result).
    files = existing + top_k extra by fused score."""
    comp_short = claim.replace("_completion", "")
    g = compute_gap(repo, claim, case_file, sym)
    gap_cands = candidates_for_gap(repo, g, existing)
    cand_scored_det = []
    for o in gap_cands:
        s, _ = file_sensitivity(repo, o.target_tests, sym, kind)
        cov = len(set(o.covered_contracts) & set(g.required_contracts)) / max(1, len(g.required_contracts))
        cand_scored_det.append((o, s, cov, 0.0, None))
    top1_sens = max((s for _, s, _, _, _ in cand_scored_det), default=0.0)
    # private + indirect signals (cheap)
    changed_is_private = sym.split(".")[-1].startswith("_")
    # near-duplicate tied check
    indirect = False

    llm_result = None
    llm_scores = {}   # obligation_id -> semantic_relevance
    if should_invoke_llm(cand_scored_det, top1_sens, changed_is_private, indirect):
        pool = _candidate_pool_for_llm(repo, case_file, sym, kind, claim, existing)
        if pool:
            prompt = build_prompt(repo, case_file, sym, kind, claim, existing, pool, prompt_variant)
            llm_result = call_llm(prompt)
            if "candidates" in llm_result:
                for c in llm_result["candidates"]:
                    oid = c.get("obligation_id")
                    rel = c.get("semantic_relevance", 0.0)
                    if isinstance(rel, (int, float)):
                        llm_scores[oid] = float(rel)
                LLM_INVOCATIONS.append({
                    "repo": repo, "symbol": sym, "kind": kind,
                    "prompt_variant": prompt_variant,
                    "n_candidates_in_pool": len(pool),
                    "n_llm_candidates": len(llm_result["candidates"]),
                    "tokens_in": llm_result.get("tokens_in"),
                    "tokens_out": llm_result.get("tokens_out"),
                    "latency_ms": llm_result.get("latency_ms"),
                    "uncertain": llm_result.get("uncertain"),
                })
            else:
                LLM_INVOCATIONS.append({"repo": repo, "symbol": sym, "kind": kind,
                                        "prompt_variant": prompt_variant,
                                        "error": llm_result.get("error", "unknown")[:80]})
    # fuse scores over the union of det candidates + LLM-named candidates
    union = {o.obligation_id: o for o in gap_cands}
    for oid in llm_scores:
        for o in build_pool(repo):
            if o.obligation_id == oid and oid not in union:
                union[oid] = o
    scored = []
    for oid, o in union.items():
        if o.target_tests in existing:
            continue
        s, _ = file_sensitivity(repo, o.target_tests, sym, kind)
        cov = len(set(o.covered_contracts) & set(g.required_contracts)) / max(1, len(g.required_contracts))
        llm_s = llm_scores.get(oid, 0.0)
        fused = w_assertion * s + w_coverage * cov + w_llm * llm_s - 0.10 * (o.estimated_cost / 2.0)
        scored.append((o, fused, s, cov, llm_s))
    scored.sort(key=lambda x: -x[1])
    picked = [o for o, _, _, _, _ in scored[:top_k]]
    extra = [o.target_tests for o in picked if o.target_tests not in existing]
    files = list(dict.fromkeys(existing + extra))
    return files, bool(llm_scores), llm_result


if __name__ == "__main__":
    # smoke on cec02 (clear_caches) which deterministic missed (FAIL in legacy)
    existing = EXISTING_VERIFY_SET["cerberus"]["testing"]
    files, used, res = hybrid_select("cerberus", "cerberus/validator.py",
                                     "BareValidator.clear_caches", "ADD_OPTIONAL_PARAM",
                                     "testing_completion", existing, top_k=2)
    print("cec02 hybrid files:", files, "| llm_used:", used)
    if res and "candidates" in res:
        print("LLM candidates:", [(c["obligation_id"], c.get("semantic_relevance")) for c in res["candidates"]])