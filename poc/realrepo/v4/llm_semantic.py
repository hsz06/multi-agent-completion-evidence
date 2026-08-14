"""v4.2 LLM Semantic Candidate Augmenter.

Calls an Anthropic-compatible Messages endpoint (local GLM-5.2 proxy in this
env) ONLY to propose semantically-relevant existing verification obligations
for a contract change. NEVER lets the LLM see which test actually FAILs.

Strict anti-leakage:
  - LLM input contains: changed contract symbol/type/kind, a min diff summary,
    caller/callee context, the completion claim, existing verify-set, and a
    candidate pool (<=10) with test names + source snippets + coverage/assert
    summaries + runtime cost. NO held-out PASS/FAIL.
  - LLM output: JSON { candidates:[obligation_id,semantic_relevance,reason], uncertain }
  - Final detection is always decided by real pytest / held-out matrix.

If no endpoint/token is configured, falls back to deterministic heuristic and
records LLM_SELECTOR_NOT_AVAILABLE.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "v3"))
sys.path.insert(0, str(_HERE))

PROMPT_VERSION = "v42-semantic-001"
MODEL = os.environ.get("ANTHROPIC_MODEL", "GLM-5.2")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "") or os.environ.get("ANTHROPIC_API_KEY", "")

try:
    import requests as _requests
except Exception:
    _requests = None

# evidence import for assertion summary
from assertion import file_sensitivity
from engine import REPOS_DIR


def llm_available() -> bool:
    return bool(BASE_URL and TOKEN and _requests is not None)


def _min_diff(repo: str, file_rel: str, symbol: str, kind: str) -> str:
    """A short textual description of the change (not the real diff lines,
    which could leak). Symbol + kind + a 1-line semantic description."""
    return f"{file_rel} :: {symbol} :: {kind}"


def _test_snippet(repo: str, testfile: str, max_lines: int = 12) -> str:
    p = REPOS_DIR / repo / testfile
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    # take the first few test-function bodies as a representative snippet
    out = []
    started = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("def test_") or s.startswith("    def test_"):
            started = True
        if started:
            out.append(ln)
            if len(out) >= max_lines:
                break
    return "\n".join(out) if out else "\n".join(lines[:max_lines])


def _coverage_summary(o) -> str:
    return (f"covers {len(o.covered_contracts)} contracts in "
            f"{','.join(sorted(set(c.split('::')[0] for c in o.covered_contracts)))}")


def _assertion_summary(repo, testfile, sym, kind) -> str:
    s, ev = file_sensitivity(repo, testfile, sym, kind)
    parts = []
    if ev.get("direct_calls"): parts.append(f"calls {sym.split('.')[-1]}")
    if ev.get("return_flows_to_assert"): parts.append("asserts return value")
    if ev.get("isinstance_assert"): parts.append("isinstance assert")
    if ev.get("pytest_raises_around_call"): parts.append("pytest.raises")
    if ev.get("field_index_in_assert"): parts.append("field/index assert")
    return "; ".join(parts) or "no direct assertion on changed symbol"


def build_prompt(repo: str, changed_file: str, changed_symbol: str, kind: str,
                 claim: str, existing: list, candidate_pool: list, prompt_variant="C") -> str:
    """Build the LLM prompt. Variants A (names only), B (+snippets),
    C (+assertion+caller context). Default C."""
    changed = {
        "symbol": changed_symbol, "contract_type": "FUNCTION_SIGNATURE",
        "change_kind": kind,
        "diff_summary": _min_diff(repo, changed_file, changed_symbol, kind),
    }
    cands = []
    for o in candidate_pool[:10]:
        entry = {"test_id": o.target_tests,
                 "runtime_cost": round(o.estimated_cost, 3),
                 "static_relation": _coverage_summary(o)}
        if prompt_variant in ("B", "C"):
            entry["source_snippet"] = _test_snippet(repo, o.target_tests, 10)
        if prompt_variant == "C":
            entry["assertion_summary"] = _assertion_summary(
                repo, o.target_tests, changed_symbol, kind)
        cands.append(entry)
    payload = {
        "changed_contract": changed,
        "affected_completion": claim,
        "existing_verify_set": existing,
        "candidate_verifiers": cands,
        "instruction": ("Identify which existing verification obligations are "
                        "MOST semantically relevant to this contract change — "
                        "i.e. which tests likely exercise or depend on the "
                        "changed behavior. Do NOT predict which test will FAIL. "
                        "Reply ONLY with JSON: "
                        '{"candidates":[{"obligation_id":..,"semantic_relevance":0..1,'
                        '"reason":..}],"uncertain":bool}. Max 3 candidates.'),
        "constraint": "You must not output any judgment about whether any test passes or fails.",
    }
    return json.dumps(payload, indent=2)


def call_llm(prompt: str) -> dict:
    """Call the endpoint. Returns {candidates, uncertain, tokens_in, tokens_out, latency_ms}
    or {error} on failure."""
    if not llm_available():
        return {"error": "LLM_SELECTOR_NOT_AVAILABLE"}
    t0 = time.time()
    try:
        r = _requests.post(
            BASE_URL + "/v1/messages",
            headers={"x-api-key": TOKEN, "anthropic-version": "2023-06-01",
                     "content-type": "application/json",
                     "authorization": "Bearer " + TOKEN},
            json={"model": MODEL, "max_tokens": 512, "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        lat = (time.time() - t0) * 1000
        if r.status_code != 200:
            return {"error": f"http {r.status_code}: {r.text[:200]}", "latency_ms": lat}
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        # parse JSON from text (model may wrap in ```json fences)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        parsed = None
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
        if not parsed:
            return {"error": "unparseable", "raw": text[:300],
                    "latency_ms": lat, "tokens_in": usage.get("input_tokens"),
                    "tokens_out": usage.get("output_tokens")}
        return {"candidates": parsed.get("candidates", []),
                "uncertain": parsed.get("uncertain", False),
                "tokens_in": usage.get("input_tokens"),
                "tokens_out": usage.get("output_tokens"),
                "latency_ms": round(lat, 1)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


if __name__ == "__main__":
    print("llm_available:", llm_available(), "| model:", MODEL, "| base:", BASE_URL)
    # self-test: simple prompt, verify no oracle-label leakage in the prompt builder
    from obligation.pool import build_pool
    pool = build_pool("cerberus")
    p = build_prompt("cerberus", "cerberus/validator.py", "BareValidator.clear_caches",
                     "ADD_OPTIONAL_PARAM", "testing_completion",
                     ["cerberus/tests/test_normalization.py"],
                     pool, "C")
    # assert no FAIL/oracle leakage
    assert "per_file" not in p, "leakage: held-out matrix referenced"
    # FAIL may appear only as the instruction forbidding the LLM from
    # predicting it — that is a constraint, not a leaked label.
    import re as _re
    # ensure no test's PASS/FAIL status is embedded
    assert not _re.search(r'"(pass|fail)":\s*"', p, _re.I), "leakage: test status embedded"
    print("prompt (truncated):", p[:400])
    if llm_available():
        r = call_llm(p)
        print("response:", json.dumps(r, indent=2)[:600])