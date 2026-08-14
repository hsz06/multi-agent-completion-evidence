"""Backend agent: owns /user API schema + backend code."""
from __future__ import annotations

from core.models import (
    Agent, Task, Artifact, Evidence, CompletionClaim, ClaimStatus,
    ArtifactType, ID_API_SCHEMA, ID_BACKEND_CODE, ID_BACKEND_COMPLETION,
)

AGENT_ID = "agent-backend"
TASK_ID = "task-backend"

# --- artifact contents -----------------------------------------------------

SCHEMA_V1 = {
    "fields": {"name": "string", "age": "int"},
    "required": ["name", "age"],
}

# Breaking: name -> username
SCHEMA_V2_BREAKING = {
    "fields": {"username": "string", "age": "int"},
    "required": ["username", "age"],
}

# Backward compatible: + optional avatar
SCHEMA_V2_COMPATIBLE = {
    "fields": {"name": "string", "age": "int", "avatar": "string|null"},
    "required": ["name", "age"],
}

BACKEND_CODE_V1 = """\
def get_user():
    logger.info("serving /user")
    return {"name": "hsz", "age": 25}
"""

# Case A: harmless log change only
BACKEND_CODE_V2_LOG = """\
def get_user():
    logger.debug("serving /user")
    return {"name": "hsz", "age": 25}
"""

BACKEND_CODE_V2_USERNAME = """\
def get_user():
    logger.info("serving /user")
    return {"username": "hsz", "age": 25}
"""


class BackendAgent:
    def __init__(self):
        self.agent = Agent(AGENT_ID, "backend")

    def implement_v1(self, world):
        world.add_task(Task(TASK_ID, AGENT_ID, ClaimStatus.VERIFIED.value))
        world.add_artifact(Artifact.create(
            ID_API_SCHEMA, ArtifactType.API_SCHEMA, TASK_ID, SCHEMA_V1,
            metadata={"endpoint": "/user"}))
        world.add_artifact(Artifact.create(
            ID_BACKEND_CODE, ArtifactType.BACKEND_CODE, TASK_ID, BACKEND_CODE_V1))
        ev = Evidence("EV-backend-unit-v1", "unit_test", ID_BACKEND_CODE, 1,
                      "PASS", AGENT_ID)
        world.add_evidence(ev)
        world.add_claim(CompletionClaim(
            ID_BACKEND_COMPLETION, TASK_ID, AGENT_ID, ClaimStatus.VERIFIED.value,
            evidence_ids=[ev.evidence_id],
            based_on_artifact_versions={ID_API_SCHEMA: 1, ID_BACKEND_CODE: 1}))
