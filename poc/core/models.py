"""Core data model for the completion-evidence-invalidation PoC.

Pure dataclasses, no database, no framework. Everything is JSON-serializable
so badcases and experiment logs can be dumped deterministically.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum


# ---------------------------------------------------------------------------
# Enums / constants
# ---------------------------------------------------------------------------

class ClaimStatus(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    FAILED = "FAILED"


class ChangeType(str, Enum):
    NON_SEMANTIC = "NON_SEMANTIC"                # e.g. logger.info -> logger.debug
    BACKWARD_COMPATIBLE = "BACKWARD_COMPATIBLE"  # e.g. add an optional field
    BREAKING = "BREAKING"                        # e.g. rename/remove a field


class ArtifactType:
    API_SCHEMA = "api_schema"
    BACKEND_CODE = "backend_code"
    FRONTEND_CODE = "frontend_code"
    TEST_SUITE = "test_suite"


# Stable IDs used across all scenarios. They intentionally read like the
# dependency arrows in the experiment spec: API_SCHEMA -> FRONTEND_COMPLETION.
ID_API_SCHEMA = "API_SCHEMA"
ID_BACKEND_CODE = "BACKEND_CODE"
ID_FRONTEND_CODE = "FRONTEND_CODE"
ID_TEST_SUITE = "TEST_SUITE"

ID_BACKEND_COMPLETION = "BACKEND_COMPLETION"
ID_FRONTEND_COMPLETION = "FRONTEND_COMPLETION"
ID_TESTING_COMPLETION = "TESTING_COMPLETION"

DOWNSTREAM_CLAIMS = (ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION)


def content_hash(content) -> str:
    blob = json.dumps(content, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

@dataclass
class Agent:
    agent_id: str
    role: str


@dataclass
class Task:
    task_id: str
    owner_agent: str
    status: str = ClaimStatus.PENDING.value
    version: int = 1


@dataclass
class Artifact:
    artifact_id: str
    artifact_type: str
    version: int
    content_hash: str
    producer_task: str
    metadata: dict = field(default_factory=dict)
    content: object = None

    @classmethod
    def create(cls, artifact_id, artifact_type, producer_task, content,
               metadata=None, version=1):
        return cls(artifact_id, artifact_type, version, content_hash(content),
                   producer_task, metadata or {}, content)

    def bump(self, new_content, metadata=None):
        self.version += 1
        self.content = new_content
        self.content_hash = content_hash(new_content)
        if metadata is not None:
            self.metadata = metadata


@dataclass
class Evidence:
    evidence_id: str
    evidence_type: str
    target_artifact: str
    artifact_version: int
    result: str          # "PASS" | "FAIL"
    created_by: str


@dataclass
class CompletionClaim:
    claim_id: str
    task_id: str
    agent_id: str
    status: str
    evidence_ids: list = field(default_factory=list)
    based_on_artifact_versions: dict = field(default_factory=dict)  # artifact_id -> version


@dataclass
class Dependency:
    """One edge of the cross-agent task-artifact-evidence-completion graph.

    relation_type: "artifact->artifact" | "artifact->evidence" |
                   "artifact->claim" | "task->task"
    scope: change types that trigger propagation along this edge.
    """
    source: str
    target: str
    relation_type: str
    scope: frozenset = frozenset({ChangeType.BREAKING})

    def to_dict(self):
        return {
            "source": self.source,
            "target": self.target,
            "relation_type": self.relation_type,
            "scope": sorted(s.value for s in self.scope),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["source"], d["target"], d["relation_type"],
                   frozenset(ChangeType(s) for s in d.get("scope", ["BREAKING"])))

    def key(self):
        return (self.source, self.target, self.relation_type)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def to_jsonable(obj):
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(x) for x in obj)
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    return obj
