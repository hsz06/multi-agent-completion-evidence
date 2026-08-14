"""Badcase: a recorded execution trace of a global false completion."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Badcase:
    run_id: str
    changed_artifact: str
    old_version: int
    new_version: int
    change_type: str
    new_content: object                     # needed to replay the same change
    completion_claims_before_change: dict   # claim_id -> {status, based_on}
    completion_claims_after_change: dict
    hidden_test_result: str
    global_completion: str
    global_false_completion: bool
    current_dependencies: list              # dependency dicts at failure time
    strategy: str
    world_snapshot: dict                    # full state just BEFORE the change

    @staticmethod
    def claims_view(world):
        return {
            cid: {"status": c.status,
                  "agent_id": c.agent_id,
                  "based_on_artifact_versions": dict(c.based_on_artifact_versions)}
            for cid, c in world.claims.items()
        }

    def to_dict(self):
        return asdict(self)
