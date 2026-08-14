"""Shared scenario builders: worlds, dependency graphs, change cases."""
from __future__ import annotations

from agents.backend import (
    SCHEMA_V2_BREAKING, SCHEMA_V2_COMPATIBLE, BACKEND_CODE_V2_LOG,
)
from agents.coordinator import CoordinatorAgent
from core.dependency_graph import DependencyGraph
from core.models import (
    ChangeType, Dependency,
    ID_API_SCHEMA, ID_BACKEND_CODE,
    ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION,
)
from core.world import World

# ---------------------------------------------------------------------------
# Dependency graphs
# ---------------------------------------------------------------------------

def full_graph() -> DependencyGraph:
    """The correct cross-agent dependency graph."""
    return DependencyGraph([
        Dependency(ID_API_SCHEMA, ID_FRONTEND_COMPLETION, "artifact->claim",
                   frozenset({ChangeType.BREAKING})),
        Dependency(ID_API_SCHEMA, ID_TESTING_COMPLETION, "artifact->claim",
                   frozenset({ChangeType.BREAKING})),
    ])


def broken_graph() -> DependencyGraph:
    """Experiment 4: the API_SCHEMA -> FRONTEND_COMPLETION edge is missing."""
    g = full_graph()
    g.remove(ID_API_SCHEMA, ID_FRONTEND_COMPLETION, "artifact->claim")
    return g


# ---------------------------------------------------------------------------
# World builder
# ---------------------------------------------------------------------------

def build_world(strategy: str, graph: DependencyGraph | None = None) -> World:
    """Fresh world after the initial pipeline; all claims VERIFIED."""
    world = World(graph if graph is not None else full_graph(), strategy)
    CoordinatorAgent.bootstrap(world)
    return world


def attach_revalidators(world: World):
    """Attach fresh agent revalidators to an existing/restored world
    (does not touch artifacts or claims)."""
    from agents.frontend import FrontendAgent
    from agents.testing import TestingAgent
    frontend, testing = FrontendAgent(), TestingAgent()
    world.revalidators = {
        ID_FRONTEND_COMPLETION: frontend.revalidate,
        ID_TESTING_COMPLETION: testing.revalidate,
    }


# ---------------------------------------------------------------------------
# Change cases (Experiment 3 + regression suite)
# ---------------------------------------------------------------------------

CHANGE_CASES = [
    {
        "case_id": "caseA_harmless_log",
        "artifact": ID_BACKEND_CODE,
        "new_content": BACKEND_CODE_V2_LOG,
        "expected_change_type": ChangeType.NON_SEMANTIC,
        # ground truth: nothing should be invalidated
        "gt_invalidated": set(),
    },
    {
        "case_id": "caseB_breaking_rename",
        "artifact": ID_API_SCHEMA,
        "new_content": SCHEMA_V2_BREAKING,
        "expected_change_type": ChangeType.BREAKING,
        "gt_invalidated": {ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION},
    },
    {
        "case_id": "caseC_optional_field",
        "artifact": ID_API_SCHEMA,
        "new_content": SCHEMA_V2_COMPATIBLE,
        "expected_change_type": ChangeType.BACKWARD_COMPATIBLE,
        "gt_invalidated": set(),
    },
]

ALL_CLAIM_SLOTS = {ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION}
