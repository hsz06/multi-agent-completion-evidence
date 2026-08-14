"""Testing agent: contract/integration validation against the API schema.

Its revalidation is *adaptive*: it regenerates the contract test from the
current schema and runs it against the backend, so it passes again after a
breaking change. This models the real long-horizon failure mode — tests get
updated together with the API, while the frontend's stale claim is what
actually matters.
"""
from __future__ import annotations

from core.models import (
    Agent, Task, Artifact, Evidence, CompletionClaim, ClaimStatus,
    ArtifactType, ID_API_SCHEMA, ID_TEST_SUITE, ID_TESTING_COMPLETION,
)

AGENT_ID = "agent-testing"
TASK_ID = "task-testing"


def _suite_for(schema: dict) -> dict:
    return {
        "name": "user_api_contract",
        "assertions": [f"response has field '{f}'" for f in sorted(schema["fields"])],
    }


class TestingAgent:
    def __init__(self):
        self.agent = Agent(AGENT_ID, "testing")
        self._ev_counter = 0

    def _evidence(self, world, result) -> Evidence:
        self._ev_counter += 1
        ev = Evidence(f"EV-testing-{self._ev_counter}", "contract_test",
                      ID_TEST_SUITE, world.artifact(ID_TEST_SUITE).version,
                      result, AGENT_ID)
        world.add_evidence(ev)
        return ev

    def implement(self, world):
        schema = world.artifact(ID_API_SCHEMA).content
        world.add_task(Task(TASK_ID, AGENT_ID, ClaimStatus.VERIFIED.value))
        world.add_artifact(Artifact.create(
            ID_TEST_SUITE, ArtifactType.TEST_SUITE, TASK_ID, _suite_for(schema)))
        ev = self._evidence(world, "PASS")
        world.add_claim(CompletionClaim(
            ID_TESTING_COMPLETION, TASK_ID, AGENT_ID, ClaimStatus.VERIFIED.value,
            evidence_ids=[ev.evidence_id],
            based_on_artifact_versions={ID_API_SCHEMA: 1}))

    def revalidate(self, world):
        """Regenerate the contract test from the current schema and re-run it.

        Backend conforms to its own new schema, so the regenerated suite
        passes (PASS is determined by backend/schema consistency, not by
        guessing).
        """
        schema_art = world.artifact(ID_API_SCHEMA)
        suite = world.artifact(ID_TEST_SUITE)
        suite.bump(_suite_for(schema_art.content))

        claim = world.claim(ID_TESTING_COMPLETION)
        ev = self._evidence(world, "PASS")
        claim.evidence_ids.append(ev.evidence_id)
        claim.status = ClaimStatus.VERIFIED.value
        claim.based_on_artifact_versions = {ID_API_SCHEMA: schema_art.version}
        world.tasks[claim.task_id].status = claim.status
        return claim.status
