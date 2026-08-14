"""v4.2 full experiment driver: 6+ strategies, cerberus专项, failure归因 A-E,
LLM prompt ablation, SemanticRescueRate / PrivateContractRescueRate, weight
sensitivity. Emits the 8 v4.2 result files. Does NOT overwrite v4/v4.1.

LLM is real (GLM-5.2) only for proposals; held-out per_file matrix is the
sole detection oracle and never read by the selector.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))

from engine import REPOS_CFG, build_change_registry
from config import POOL_FILES, EXISTING_VERIFY_SET
from obligation.pool import build_pool
from obligation.coverage import compute_gap, candidates_for_gap, covered_by_files
from assertion import file_sensitivity
from assertion_selector import assertion_aware_select, coverage_only_select
from hybrid_selector import hybrid_select, LLM_INVOCATIONS, should_invoke_llm, llm_available
from v42_deterministic import private_aware_select, run_v42a, dump_private_contracts
from strategies import (assemble_cases, _file_fails, _cost, _detects, ORACLE,
                        DOWNSTREAM)

RESULTS = _HERE / "results"


def _write_csv(name, rows, fields):
    with open(RESULTS / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _integ_cost(repo):
    return _cost(repo, POOL_FILES[repo])


# ---------------------------------------------------------------------------
# per-strategy runner
# ---------------------------------------------------------------------------

def _select_for(strategy, c, threshold=1.0):
    comp = c["claim"].replace("_completion", "")
    existing = list(EXISTING_VERIFY_SET[c["repo"]].get(comp, []))
    early_stopped = False
    llm_used = False
    if strategy == "Local":
        return existing, False, False
    if strategy == "IntegrationAll":
        return list(POOL_FILES[c["repo"]]), False, False
    # early-stop gate (for all non-baseline proposed strategies)
    if any(_file_fails(c["case_id"], f) for f in existing):
        return existing, True, False
    if strategy == "v4.1 (AssertionTop1+EarlyStop)":
        g = compute_gap(c["repo"], c["claim"], c["file"], c["symbol"])
        cands = candidates_for_gap(c["repo"], g, existing)
        scored = [(o, file_sensitivity(c["repo"], o.target_tests, c["symbol"], c["kind"])[0])
                  for o in cands]
        scored.sort(key=lambda x: -x[1])
        top = [scored[0][0]] if scored else []
        extra = [o.target_tests for o in top if o.target_tests not in existing]
        return list(dict.fromkeys(existing + extra)), True, False
    if strategy == "PrivateContract":
        files, _ = private_aware_select(c["repo"], c["file"], c["symbol"], c["kind"],
                                        c["claim"], top1=True)
        return files, True, False
    if strategy == "LLMOnly":
        # LLM-only semantic pick: top1 by LLM relevance (no coverage/assert fusion)
        files, used, _ = hybrid_select(c["repo"], c["file"], c["symbol"], c["kind"],
                                       c["claim"], existing,
                                       w_assertion=0.0, w_coverage=0.0, w_llm=1.0,
                                       top_k=1, prompt_variant="C")
        return files, True, used
    if strategy == "Hybrid":
        files, used, _ = hybrid_select(c["repo"], c["file"], c["symbol"], c["kind"],
                                       c["claim"], existing, top_k=1, prompt_variant="C")
        return files, True, used
    if strategy == "HybridTop2":
        files, used, _ = hybrid_select(c["repo"], c["file"], c["symbol"], c["kind"],
                                       c["claim"], existing, top_k=2, prompt_variant="C")
        return files, True, used
    raise ValueError(strategy)


def run_all(cases):
    strategies = ["Local", "IntegrationAll", "v4.1 (AssertionTop1+EarlyStop)",
                  "PrivateContract", "LLMOnly", "Hybrid", "HybridTop2"]
    n_break = sum(1 for c in cases if c["true_break"]) or 1
    agg = {}
    per_case = []
    for strat in strategies:
        det = 0; ncount = 0; cost = 0.0; relcost = 0.0; es_hits = 0; llm_calls = 0
        per_repo = defaultdict(lambda: {"n": 0, "det": 0})
        for c in cases:
            files, es, used = _select_for(strat, c)
            detect = _detects(c, files)
            if c["true_break"] and detect:
                det += 1
            ncount += len(files)
            cc = _cost(c["repo"], files)
            cost += cc
            relcost += cc / max(_integ_cost(c["repo"]), 0.001)
            if es: es_hits += 1
            if used: llm_calls += 1
            per_repo[c["repo"]]["n"] += int(c["true_break"])
            if c["true_break"] and detect:
                per_repo[c["repo"]]["det"] += 1
            per_case.append({"strategy": strat, "case_id": c["case_id"],
                             "repo": c["repo"], "true_break": c["true_break"],
                             "detect": int(detect), "n_files": len(files),
                             "early_stopped": int(es), "llm_used": int(used)})
        agg[strat] = {
            "detection_rate": round(det / n_break, 4),
            "vrr": round(det / n_break, 4),
            "avg_test_count": round(ncount / len(cases), 2),
            "avg_relative_cost": round(relcost / len(cases), 4),
            "early_stop_hits": es_hits,
            "llm_calls": llm_calls,
            "llm_invocation_rate": round(llm_calls / len(cases), 4),
            "per_repo": {k: round(v["det"]/max(v["n"],1),4) for k,v in per_repo.items()},
        }
    return agg, per_case


# ---------------------------------------------------------------------------
# SemanticRescueRate / PrivateContractRescueRate + failure归因
# ---------------------------------------------------------------------------

def rescue_analysis(cases):
    v41_missed = []
    hybrid_rescued = []
    private_rescued = []
    breakdown = []
    for c in cases:
        if not c["true_break"]:
            continue
        # v4.1 select
        v41_files, _, _ = _select_for("v4.1 (AssertionTop1+EarlyStop)", c)
        v41_det = _detects(c, v41_files)
        # private only
        priv_files, _, _ = _select_for("PrivateContract", c)
        priv_det = _detects(c, priv_files)
        # hybrid
        hyb_files, _, _ = _select_for("Hybrid", c)
        hyb_det = _detects(c, hyb_files)
        if not v41_det:
            v41_missed.append(c)
            if hyb_det:
                hybrid_rescued.append(c)
            if priv_det and not v41_det:
                private_rescued.append(c)
        # failure归因 for Hybrid-missed
        if not hyb_det:
            # was legacy (test that FAILs) in LLM pool?
            fclass = classify_failure(c, hyb_files)
            breakdown.append({"case_id": c["case_id"], "repo": c["repo"],
                              "claim": c["claim"], "symbol": c["symbol"],
                              "failure_class": fclass})
    srr = round(len(hybrid_rescued) / max(len(v41_missed), 1), 4)
    pcrr = round(len(private_rescued) / max(len(v41_missed), 1), 4)
    return {"v41_missed": len(v41_missed), "hybrid_rescued": len(hybrid_rescued),
            "private_rescued": len(private_rescued),
            "semantic_rescue_rate": srr,
            "private_contract_rescue_rate": pcrr,
            "hybrid_missed_breakdown": breakdown}


def classify_failure(c, hyb_files):
    """A-E per spec §20."""
    integ = [_file_fails(c["case_id"], f) for f in POOL_FILES[c["repo"]]]
    if not any(integ):
        return "E"   # Pool ceiling: no test detects (rare in this dataset)
    # is the FAILing test in the LLM pool?
    from hybrid_selector import _candidate_pool_for_llm
    comp = c["claim"].replace("_completion", "")
    existing = list(EXISTING_VERIFY_SET[c["repo"]].get(comp, []))
    pool = _candidate_pool_for_llm(c["repo"], c["file"], c["symbol"], c["kind"],
                                   c["claim"], existing)
    pool_files = {o.target_tests for o in pool}
    fail_files = {f for f in POOL_FILES[c["repo"]] if _file_fails(c["case_id"], f)}
    if not (fail_files & pool_files):
        return "B"   # Candidate blind spot: FAIL test not in candidate set
    # FAIL test in pool but not selected -> ranking failure
    selected_fail = any(_file_fails(c["case_id"], f) for f in hyb_files)
    if selected_fail:
        return "D"   # (shouldn't happen if hyb missed)
    return "C"   # Ranking failure


# ---------------------------------------------------------------------------
# LLM ablation (prompt variants)
# ---------------------------------------------------------------------------

def prompt_ablation(cases):
    rows = []
    # only run on a sample of v4.1-missed ambiguous cases to bound cost
    missed = []
    for c in cases:
        if not c["true_break"]:
            continue
        v41_files, _, _ = _select_for("v4.1 (AssertionTop1+EarlyStop)", c)
        if not _detects(c, v41_files):
            missed.append(c)
    sample = missed[:8]   # bounded
    for variant in ["A", "B", "C"]:
        det = 0; tokens = 0; n = 0
        for c in sample:
            comp = c["claim"].replace("_completion", "")
            existing = list(EXISTING_VERIFY_SET[c["repo"]].get(comp, []))
            if any(_file_fails(c["case_id"], f) for f in existing):
                det += 1; n += 1; continue
            files, used, res = hybrid_select(c["repo"], c["file"], c["symbol"], c["kind"],
                                             c["claim"], existing, top_k=1,
                                             prompt_variant=variant)
            if _detects(c, files):
                det += 1
            n += 1
            if res and res.get("tokens_in"):
                tokens += res["tokens_in"] + (res.get("tokens_out") or 0)
        rows.append({"prompt_variant": variant, "n_sample": len(sample),
                     "detection_rate": round(det / max(len(sample),1), 4),
                     "total_tokens": tokens})
    return rows


# ---------------------------------------------------------------------------
# weight sensitivity
# ---------------------------------------------------------------------------

def weight_sensitivity(cases):
    rows = []
    n_break = sum(1 for c in cases if c["true_break"]) or 1
    for w in [0.2, 0.3, 0.4]:
        det = 0; relcost = 0.0; ncount = 0
        for c in cases:
            comp = c["claim"].replace("_completion", "")
            existing = list(EXISTING_VERIFY_SET[c["repo"]].get(comp, []))
            if any(_file_fails(c["case_id"], f) for f in existing):
                files = existing
            else:
                files, used, _ = hybrid_select(c["repo"], c["file"], c["symbol"], c["kind"],
                                               c["claim"], existing, w_llm=w, top_k=1)
            if c["true_break"] and _detects(c, files):
                det += 1
            ncount += len(files)
            relcost += _cost(c["repo"], files) / max(_integ_cost(c["repo"]), 0.001)
        rows.append({"llm_weight": w, "detection_rate": round(det / n_break, 4),
                     "avg_test_count": round(ncount/len(cases),2),
                     "avg_relative_cost": round(relcost/len(cases),4)})
    return rows


def run():
    t0 = time.time()
    dump_private_contracts()
    cases = assemble_cases()
    print(f"[v4.2] cases={len(cases)} llm_available={llm_available()}", flush=True)
    agg, per_case = run_all(cases)
    _write_csv("v42_detection_cost.csv",
               [{"strategy": k, **{kk:vv for kk,vv in v.items() if kk!='per_repo'},
                 "per_repo": json.dumps(v.get('per_repo',{}))} for k,v in agg.items()],
               ["strategy","detection_rate","vrr","avg_test_count","avg_relative_cost",
                "early_stop_hits","llm_calls","llm_invocation_rate","per_repo"])
    # cerberus专项
    cerb_rows = [{"strategy": k, "cerberus_detection": v["per_repo"].get("cerberus", 0),
                  "cerberus_cost": None} for k,v in agg.items()]
    _write_csv("v42_cerberus_analysis.csv", cerb_rows, ["strategy","cerberus_detection","cerberus_cost"])
    # rescue + failure归因
    rescue = rescue_analysis(cases)
    json.dump({**rescue, "hybrid_missed_breakdown": rescue["hybrid_missed_breakdown"]},
              open(RESULTS / "v42_failure_analysis.json", "w"), indent=2)
    # LLM candidates log
    with open(RESULTS / "v42_llm_candidates.jsonl", "w") as f:
        for entry in LLM_INVOCATIONS:
            f.write(json.dumps(entry) + "\n")
    # prompt ablation
    pa = prompt_ablation(cases)
    _write_csv("v42_llm_ablation.csv", pa, ["prompt_variant","n_sample","detection_rate","total_tokens"])
    # weight sensitivity
    ws = weight_sensitivity(cases)
    _write_csv("v42_weight_sensitivity.csv", ws, ["llm_weight","detection_rate","avg_test_count","avg_relative_cost"])
    # private contract deterministic already in v42_private_contract.csv (from v42_deterministic)
    # summary
    summary = {
        "llm_available": llm_available(), "model": "GLM-5.2" if llm_available() else None,
        "prompt_version": "v42-semantic-001",
        "n_cases": len(cases),
        "aggregate": agg,
        "rescue": rescue,
        "prompt_ablation": pa,
        "weight_sensitivity": ws,
        "n_llm_invocations": len(LLM_INVOCATIONS),
    }
    json.dump(summary, open(RESULTS / "v42_summary.json", "w"), indent=2)
    # headline
    print(f"\n{'Strategy':<36}{'Det':>6}{'VRR':>6}{'#T':>5}{'Cost':>7}{'LLM':>6}")
    for k,v in agg.items():
        print(f"{k:<36}{v['detection_rate']:>6.3f}{v['vrr']:>6.3f}"
              f"{v['avg_test_count']:>5.2f}{v['avg_relative_cost']:>7.3f}{v['llm_calls']:>6}")
    print(f"\nRescue: v41_missed={rescue['v41_missed']} hybrid_rescued={rescue['hybrid_rescued']} "
          f"SRR={rescue['semantic_rescue_rate']} PCRR={rescue['private_contract_rescue_rate']}")
    print(f"LLM invocations: {len(LLM_INVOCATIONS)} (rate {len(LLM_INVOCATIONS)/len(cases):.2f})")
    print(f"failure breakdown: {Counter(b['failure_class'] for b in rescue['hybrid_missed_breakdown'])}")
    print(f"[done] v4.2 in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    run()