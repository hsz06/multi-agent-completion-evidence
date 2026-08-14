"""v4.2 unit tests: LLM prompt oracle-isolation + private-contract no-oracle +
hybrid selector read-exclusion. Deterministic where possible.
Run: python3 -m pytest v4/tests/test_v42.py -q"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # realrepo
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "v3"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # v4


def test_llm_prompt_has_no_oracle_labels():
    """The LLM prompt MUST NOT embed held-out PASS/FAIL labels."""
    from obligation.pool import build_pool
    from llm_semantic import build_prompt
    pool = build_pool("cerberus")
    p = build_prompt("cerberus", "cerberus/validator.py",
                     "BareValidator.clear_caches", "ADD_OPTIONAL_PARAM",
                     "testing_completion",
                     ["cerberus/tests/test_normalization.py"], pool, "C")
    # no reference to the held-out matrix or files' results
    assert "per_file" not in p
    assert "evaluation_private" not in p
    # FAIL may only appear inside the instruction forbidding prediction;
    # we additionally forbid a structured status field
    import re
    assert not re.search(r'"(passed|failed|result)":\s*"', p, re.I), \
        "prompt embedded a test status label"


def test_llm_selector_cannot_open_evaluation_private():
    """The LLM client/selector modules must not READ the held-out oracle files.
    Forbidden: referencing per_file.json / evaluation_private_oracle paths in
    code (docstring prose about not reading them is fine)."""
    import llm_semantic, hybrid_selector
    import re
    for mod in (llm_semantic, hybrid_selector):
        src = Path(mod.__file__).read_text()
        # remove docstrings so prose about "never reads oracle" doesn't trip it
        code_only = re.sub(r'""".*?"""', '', src, flags=re.S)
        code_only = re.sub(r"'''(.*?)'''", '', code_only, flags=re.S)
        assert 'per_file.json' not in code_only, \
            f"{mod.__name__} references held-out per_file.json in code"
        assert 'evaluation_private_oracle' not in code_only, \
            f"{mod.__name__} references oracle dir in code"


def test_private_contract_extractor_no_oracle_dependency():
    """Private contract extraction uses only changed-symbol/dynamic/public-call
    signals from pristine source — never the held-out oracle."""
    import private_contract
    src = Path(private_contract.__file__).read_text()
    assert "per_file" not in src
    assert "evaluation_private" not in src
    # it must read only repo source + pristine coverage
    assert "REPOS_DIR" in src


def test_should_invoke_llm_conditions():
    from hybrid_selector import should_invoke_llm
    # no candidates -> invoke
    assert should_invoke_llm([], 0.0, False, False) is True
    # low top1 sens -> invoke
    assert should_invoke_llm([(None, 0.2)], 0.2, False, False) is True
    # confident -> do not invoke
    assert should_invoke_llm([(None, 0.8)], 0.8, False, False) is False
    # tied -> invoke
    assert should_invoke_llm([(None, 0.6), (None, 0.6)], 0.6, False, False) is True
    # private + indirect -> invoke
    assert should_invoke_llm([(None, 0.8)], 0.8, True, False) is True
    assert should_invoke_llm([(None, 0.8)], 0.8, False, True) is True


def test_private_contracts_only_with_signal():
    """Private symbols without changed/dynamic/public-call signals must NOT be added."""
    from private_contract import extract_private_contracts
    # cerberus: many private helpers; only those with signal survive
    pcs = extract_private_contracts("cerberus")
    assert all(p.extraction_reason for p in pcs)   # every node has a concrete reason
    reasons = {p.extraction_reason for p in pcs}
    assert reasons <= {"CHANGED_SYMBOL","DYNAMICALLY_COVERED","PUBLIC_CALL_PATH","MULTI_SIGNAL"}


def test_same_56_samples():
    from strategies import assemble_cases
    assert len(assemble_cases()) == 56


def test_llm_output_schema_conforms():
    """If LLM unavailable this is skipped; if available the parsed response
    must match {candidates:[{obligation_id, semantic_relevance, reason}], uncertain}."""
    from llm_semantic import llm_available, build_prompt, call_llm
    from obligation.pool import build_pool
    if not llm_available():
        import pytest
        pytest.skip("LLM endpoint not available")
    pool = build_pool("tinydb")[:5]
    r = None
    for _ in range(3):   # tolerate transient 422 from the proxy
        p = build_prompt("tinydb","tinydb/table.py","Table.insert","CHANGE_RETURN_TYPE",
                         "dev_b_completion",["tests/test_utils.py"],pool,"C")
        r = call_llm(p)
        if "candidates" in r:
            break
    assert r is not None and "candidates" in r, f"LLM call failed: {r.get('error') if r else 'no response'}"
    for c in r["candidates"]:
        assert "obligation_id" in c and "semantic_relevance" in c
        assert 0.0 <= float(c["semantic_relevance"]) <= 1.0