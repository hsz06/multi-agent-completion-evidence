"""Frontend agent: consumes /user, renders user.name."""
from __future__ import annotations

from core.models import (
    Agent, Task, Artifact, Evidence, CompletionClaim, ClaimStatus,
    ArtifactType, ID_API_SCHEMA, ID_FRONTEND_CODE, ID_FRONTEND_COMPLETION,
)

AGENT_ID = "agent-frontend"
TASK_ID = "task-frontend"

# The frontend was written against schema v1: it dereferences user.name.
CONSUMES_FIELDS = ("name",)

FRONTEND_CODE_V1 = """\
def render_profile(user):
    return f"<h1>{user['name']}</h1>"
"""


def _field_check(world) -> list[str]:
    schema_fields = set(world.artifact(ID_API_SCHEMA).content["fields"])
    return sorted(set(CONSUMES_FIELDS) - schema_fields)


class FrontendAgent:
    def __init__(self):
        self.agent = Agent(AGENT_ID, "frontend")

    def implement(self, world):
        schema_version = world.artifact(ID_API_SCHEMA).version
        world.add_task(Task(TASK_ID, AGENT_ID, ClaimStatus.VERIFIED.value))
        world.add_artifact(Artifact.create(
            ID_FRONTEND_CODE, ArtifactType.FRONTEND_CODE, TASK_ID,
            FRONTEND_CODE_V1, metadata={"consumes_fields": list(CONSUMES_FIELDS)}))
        ev = Evidence("EV-frontend-fieldcheck-v1", "field_check", ID_FRONTEND_CODE,
                      1, "PASS", AGENT_ID)
        world.add_evidence(ev)
        world.add_claim(CompletionClaim(
            ID_FRONTEND_COMPLETION, TASK_ID, AGENT_ID, ClaimStatus.VERIFIED.value,
            evidence_ids=[ev.evidence_id],
            based_on_artifact_versions={ID_API_SCHEMA: schema_version}))

    def revalidate(self, world):
        """Re-run the frontend verification against the *current* schema.

        The frontend code has not changed (it still reads user.name), so a
        breaking schema change makes this FAIL — exactly what invalidation is
        supposed to surface.
        """
        claim = world.claim(ID_FRONTEND_COMPLETION)
        missing = _field_check(world)
        version = world.artifact(ID_FRONTEND_CODE).version
        if missing:
            ev = Evidence(f"EV-frontend-reval-v{version}-fail", "field_check",
                          ID_FRONTEND_CODE, version, "FAIL", AGENT_ID)
            claim.status = ClaimStatus.FAILED.value
        else:
            ev = Evidence(f"EV-frontend-reval-v{version}-pass", "field_check",
                          ID_FRONTEND_CODE, version, "PASS", AGENT_ID)
            claim.status = ClaimStatus.VERIFIED.value
            claim.based_on_artifact_versions = {
                ID_API_SCHEMA: world.artifact(ID_API_SCHEMA).version}
        world.add_evidence(ev)
        claim.evidence_ids.append(ev.evidence_id)
        world.tasks[claim.task_id].status = claim.status
        return claim.status
