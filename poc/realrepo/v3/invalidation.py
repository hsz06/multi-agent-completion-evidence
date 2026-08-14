"""Invalidation strategies at two granularities.

Given a dependency graph (contract-level or file-level G*) and a change,
return the set of completion claim IDs that should be marked STALE.

Strategies:
  all_downstream     — any change -> every non-producer completion
  static_file        — FILE-level G* edges (ARTIFACT->COMPLETION), ignore scope
  freshness          — same as static_file (content changed => stale); kept as
                       a labelled baseline distinct from contract scope logic
  change_aware_file  — FILE-level G* edges filtered by change scope
  static_contract    — CONTRACT-level G* edges, ignore scope
  change_aware_contract — CONTRACT-level G* edges filtered by change scope
"""
from __future__ import annotations

from contracts.model import ChangeType


def _edges_to_completions(graph, producer_source, change_type, change_aware, gran=None):
    out = []
    for e in graph:
        if e.source != producer_source:
            continue
        if e.relation_type not in ("CONTRACT->COMPLETION", "ARTIFACT->COMPLETION"):
            continue
        if gran and e.granularity.value != gran:
            continue
        if change_aware and change_type not in e.scope:
            continue
        out.append(e.target)
    return list(dict.fromkeys(out))


def invalidate(strategy, graph, producer_source, change_type, producer_completions=("dev_a_completion",)):
    """Return set of completion claim IDs to mark STALE."""
    if strategy == "all_downstream":
        return {e.target for e in graph
                if e.relation_type in ("CONTRACT->COMPLETION", "ARTIFACT->COMPLETION")
                and e.target not in producer_completions} or {"dev_b_completion", "testing_completion"}
    if strategy == "static_file":
        return set(_edges_to_completions(graph, producer_source, change_type, False, gran="FILE"))
    if strategy == "freshness":
        return set(_edges_to_completions(graph, producer_source, change_type, False, gran="FILE"))
    if strategy == "change_aware_file":
        return set(_edges_to_completions(graph, producer_source, change_type, True, gran="FILE"))
    if strategy == "static_contract":
        return set(_edges_to_completions(graph, producer_source, change_type, False, gran="CONTRACT"))
    if strategy == "change_aware_contract":
        return set(_edges_to_completions(graph, producer_source, change_type, True, gran="CONTRACT"))
    raise ValueError(strategy)


FILE_STRATEGIES = ("all_downstream", "static_file", "freshness", "change_aware_file")
CONTRACT_STRATEGIES = ("all_downstream", "static_contract", "freshness", "change_aware_contract")
ALL_STRATEGIES = ("all_downstream", "static_file", "static_contract",
                  "freshness", "change_aware_file", "change_aware_contract")