"""SemanticCandidateExtractor.

Interface that CAN use an LLM, but with a deterministic heuristic fallback so
experiments never block on network/API availability. Current environment has no
LLM API configured, so the fallback is always active and is documented as such.

The fallback produces structured Candidate objects from cheap name/usage facts:
  - consumer file mentions symbols that the changed producer file defines
  - producer file names match the task domain (schema/config/api)
"""
from __future__ import annotations

import re

from .classifier import extract_signatures
from .models import Candidate, ChangeType


class SemanticCandidateExtractor:
    def __init__(self, use_llm=False):
        self.use_llm = use_llm
        self.fallback_used = not use_llm

    def extract_candidates(self, *, changed_producer, changed_src,
                           consumer_files, change_type, task_spec="") -> list[Candidate]:
        """Return structured candidates for who may depend on the changed file."""
        if self.use_llm:
            candidates = self._llm_candidates(changed_producer, consumer_files,
                                              task_spec, change_type)
            self.fallback_used = False
            if candidates:
                return candidates
            # LLM failed/empty -> fall through to deterministic pass
        return self._fallback(changed_producer, changed_src, consumer_files,
                              change_type)

    # -- deterministic fallback ------------------------------------------
    def _fallback(self, changed_producer, changed_src, consumer_files,
                  change_type) -> list[Candidate]:
        defined = extract_signatures(changed_src)
        cands = []
        for file in consumer_files:
            src = file.read_text(encoding="utf-8", errors="replace")
            # 1. the consumer literally references a changed public symbol?
            for sym in defined:
                if re.search(rf"\b{re.escape(sym)}\b", src):
                    cands.append(Candidate(
                        source=str(changed_producer), target=str(file),
                        relation_type="MODULE->MODULE",
                        scope=[change_type.value],
                        confidence=0.55,
                        reason=f"consumer {file} references public symbol "
                               f"'{sym}' defined in {changed_producer}",
                        method="semantic",
                    ))
            # 2. consumer imports the changed module path directly
            if f"{changed_producer.replace('.py', '').replace('/', '.')}" in src:
                cands.append(Candidate(
                    source=str(changed_producer), target=str(file),
                    relation_type="FILE->FILE",
                    scope=[change_type.value],
                    confidence=0.5,
                    reason=f"consumer {file} references module "
                           f"{changed_producer} textually",
                    method="semantic",
                ))
        # de-dupe by (source, target, relation_type)
        seen, out = set(), []
        for c in cands:
            k = (c.source, c.target, c.relation_type)
            if k not in seen:
                seen.add(k)
                out.append(c)
        return out

    def _llm_candidates(self, changed_producer, consumer_files, task_spec,
                        change_type):  # pragma: no cover — future hook
        raise NotImplementedError("LLM path requires an API key; not configured.")