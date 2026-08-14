"""v2 data models.

Per spec section 4: CompletionClaim must NOT carry the full dependency answer
(`based_on_artifact_versions` is gone). Dependencies are recovered from
static/dynamic/semantic evidence, not stored as an explicit answer.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ChangeType(str, Enum):
    NON_SEMANTIC = "NON_SEMANTIC"
    COMPATIBLE = "COMPATIBLE"
    POTENTIALLY_BREAKING = "POTENTIALLY_BREAKING"
    BREAKING = "BREAKING"
    UNKNOWN = "UNKNOWN"


class Status(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    FAILED = "FAILED"


class Provenance(str, Enum):
    STATIC = "STATIC"
    DYNAMIC = "DYNAMIC"
    SEMANTIC = "SEMANTIC"
    MANUAL = "MANUAL"


class RelType(str, Enum):
    FILE_TO_FILE = "FILE->FILE"
    SCHEMA_TO_CLIENT = "SCHEMA->CLIENT"
    MODULE_TO_MODULE = "MODULE->MODULE"
    CODE_TO_TEST = "CODE->TEST"
    CONFIG_TO_CODE = "CONFIG->CODE"
    ARTIFACT_TO_COMPLETION = "ARTIFACT->COMPLETION"
    TASK_TO_TASK = "TASK->TASK"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def content_hash(content) -> str:
    blob = json.dumps(content, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class Agent:
    agent_id: str
    role: str


@dataclass
class Task:
    task_id: str
    owner_agent: str
    spec: str                       # natural-language / structured task description
    status: str = Status.PENDING.value
    version: int = 1


@dataclass
class Artifact:
    """A concrete repo artifact. artifact_id is a repo-relative path."""
    artifact_id: str              # e.g. 'tinydb/table.py' or 'api/schema.json'
    artifact_type: str            # file | schema | config | ...
    version: int
    content_hash: str
    producer_task: str
    content: object = None


@dataclass
class Evidence:
    """Execution evidence for a completion — command-run facts only."""
    evidence_id: str
    command: str                  # the actual shell command executed
    files_modified: list = field(default_factory=list)
    files_observed: list = field(default_factory=list)   # imported/read files
    test_targets: list = field(default_factory=list)     # pytest node ids / files
    result: str = "PASS"                                  # PASS | FAIL
    created_by: str = ""
    artifact_hashes: dict = field(default_factory=dict)  # artifact_id -> hash at evidence time


@dataclass
class CompletionClaim:
    claim_id: str
    task_id: str
    agent_id: str
    status: str = Status.PENDING.value
    produced_artifacts: list = field(default_factory=list)  # artifact_ids
    evidence_ids: list = field(default_factory=list)
    created_at: str = ""          # wall-clock label, not used for logic


@dataclass
class Dependency:
    """Graph edge inferred from evidence — NOT an explicit answer."""
    source: str
    target: str
    relation_type: RelType = RelType.FILE_TO_FILE
    scope: frozenset = field(default_factory=lambda: frozenset(ChangeType))
    confidence: float = 0.5
    provenance: Provenance = Provenance.STATIC
    note: str = ""

    def key(self):
        return (self.source, self.target, RelType(self.relation_type).value)

    def to_dict(self):
        return {
            "source": self.source,
            "target": self.target,
            "relation_type": RelType(self.relation_type).value,
            "scope": sorted(ChangeType(s).value if not isinstance(s, str) else s
                            for s in self.scope),
            "confidence": self.confidence,
            "provenance": Provenance(self.provenance).value,
            "note": self.note,
        }

    @staticmethod
    def from_dict(d):
        return Dependency(
            source=d["source"], target=d["target"],
            relation_type=RelType(d["relation_type"]),
            scope=frozenset(ChangeType(s) for s in d["scope"]),
            confidence=d.get("confidence", 0.5),
            provenance=Provenance(d.get("provenance", "STATIC")),
            note=d.get("note", ""),
        )


@dataclass
class Candidate:
    """Structured missing-dependency candidate from any extractor."""
    source: str
    target: str
    relation_type: str
    scope: list
    confidence: float
    reason: str
    method: str                # which extractor: static|dynamic|semantic|trace|combined

    def to_dict(self):
        return asdict(self)


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