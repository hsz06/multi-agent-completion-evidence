"""Coordinator agent: builds the world, wires revalidators, applies changes."""
from __future__ import annotations

from agents.backend import BackendAgent
from agents.frontend import FrontendAgent
from agents.testing import TestingAgent
from core.change_analysis import classify_change
from core.models import ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION


class CoordinatorAgent:
    def __init__(self, agent_id="agent-coordinator"):
        self.agent_id = agent_id

    @staticmethod
    def bootstrap(world):
        """Run the initial pipeline: Backend v1 -> Frontend -> Testing.

        After this, all three claims are VERIFIED and Global = VERIFIED.
        """
        backend, frontend, testing = BackendAgent(), FrontendAgent(), TestingAgent()
        backend.implement_v1(world)
        frontend.implement(world)
        testing.implement(world)
        world.revalidators = {
            ID_FRONTEND_COMPLETION: frontend.revalidate,
            ID_TESTING_COMPLETION: testing.revalidate,
        }
        return {"backend": backend, "frontend": frontend, "testing": testing}

    @staticmethod
    def apply_change(world, artifact_id, new_content, metadata=None, change_type=None):
        """Apply an upstream change and revalidate whatever was invalidated.

        change_type defaults to the deterministic classifier output.
        """
        artifact = world.artifact(artifact_id)
        if change_type is None:
            change_type = classify_change(artifact.artifact_type,
                                          artifact.content, new_content)
        event = world.apply_change(artifact_id, new_content, change_type, metadata)
        event["revalidated"] = world.revalidate_stale(event["invalidated"])
        return event
