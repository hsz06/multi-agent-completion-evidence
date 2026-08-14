"""Trace analyzer: finds completion claims that should have been invalidated.

Rule: a claim was VERIFIED both before and after the change, but the artifact
version it based its evidence on is now behind the changed artifact's new
version — i.e. its evidence went stale and nothing marked it. Those claims
are the unexplained survivors of the failure trajectory.
"""
from __future__ import annotations

from .model import Badcase


def find_uninvalidated_dependents(badcase: Badcase) -> list[dict]:
    producer_agent = _producer_agent(badcase)
    findings = []
    for cid, after in badcase.completion_claims_after_change.items():
        if after["agent_id"] == producer_agent:
            continue  # the producer re-verifies itself as part of the change
        based_on = after["based_on_artifact_versions"]
        base_version = based_on.get(badcase.changed_artifact)
        if base_version is None or base_version >= badcase.new_version:
            continue  # doesn't depend on the changed artifact, or already rebased
        if after["status"] != "VERIFIED":
            continue  # it was invalidated or failed — propagation worked here
        edge_exists = any(
            d["source"] == badcase.changed_artifact and d["target"] == cid
            and d["relation_type"] == "artifact->claim"
            for d in badcase.current_dependencies
        )
        findings.append({
            "claim_id": cid,
            "based_on_version": base_version,
            "changed_artifact": badcase.changed_artifact,
            "new_version": badcase.new_version,
            "dependency_edge_exists": edge_exists,
        })
    return findings


def _producer_agent(badcase: Badcase) -> str:
    artifact = badcase.world_snapshot["artifacts"][badcase.changed_artifact]
    task = badcase.world_snapshot["tasks"][artifact["producer_task"]]
    return task["owner_agent"]
