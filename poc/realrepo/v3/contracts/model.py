"""v3 contract-level data model.

Granularity ladder:
  FILE < SYMBOL < CONTRACT
A CONTRACT is a typed obligation point on an artifact:
  function signature, return contract, schema field, public symbol,
  config key, type contract, API endpoint.

CompletionClaim v3 deliberately does NOT carry the dependency answer
(no based_on_artifact_versions). Dependencies are recovered from
static/dynamic/semantic evidence only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path


# make v2's common models importable for reuse
import sys as _sys
_V3 = Path(__file__).resolve().parents[2]
if str(_V3) not in _sys.path:
    _sys.path.insert(0, str(_V3))


class ContractType(str, Enum):
    FUNCTION_SIGNATURE = "FUNCTION_SIGNATURE"
    RETURN_CONTRACT = "RETURN_CONTRACT"
    SCHEMA_FIELD = "SCHEMA_FIELD"
    PUBLIC_SYMBOL = "PUBLIC_SYMBOL"
    CONFIG_KEY = "CONFIG_KEY"
    TYPE_CONTRACT = "TYPE_CONTRACT"
    API_ENDPOINT = "API_ENDPOINT"


class ChangeType(str, Enum):
    NON_SEMANTIC = "NON_SEMANTIC"
    COMPATIBLE = "COMPATIBLE"
    POTENTIALLY_BREAKING = "POTENTIALLY_BREAKING"
    BREAKING = "BREAKING"
    UNKNOWN = "UNKNOWN"


class ClaimStatus(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    FAILED = "FAILED"


class Granularity(str, Enum):
    FILE = "FILE"
    SYMBOL = "SYMBOL"
    CONTRACT = "CONTRACT"


class Provenance(str, Enum):
    STATIC = "STATIC"
    DYNAMIC = "DYNAMIC"
    SEMANTIC = "SEMANTIC"
    MANUAL = "MANUAL"


def content_hash(s) -> str:
    blob = json.dumps(s, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------------
# ContractNode
# ---------------------------------------------------------------------------

@dataclass
class ContractNode:
    contract_id: str          # f"{artifact_id}::{contract_type}::{symbol}"
    artifact_id: str          # repo-relative file path
    contract_type: ContractType
    symbol: str               # dotted, e.g. "Table.insert" or "Table.all::return" or "UserResponse.name"
    signature: dict           # type-dependent payload (params / type / default)
    location: str             # file:line
    version: int = 1
    metadata: dict = field(default_factory=dict)

    def key(self):
        return self.contract_id


# ---------------------------------------------------------------------------
# VerificationObligation
# ---------------------------------------------------------------------------

@dataclass
class VerificationObligation:
    obligation_id: str
    target_contract: str      # contract_id
    verify_command: str       # pytest target(s), ";" separated
    verify_scope: str         # LOCAL|MODULE|INTEGRATION
    owner_completion: str     # claim_id this obligation feeds
    coverage_source: str = "STATIC"   # STATIC|DYNAMIC|MANUAL


# ---------------------------------------------------------------------------
# CompletionClaim v3
# ---------------------------------------------------------------------------

@dataclass
class CompletionClaim:
    claim_id: str
    task_id: str
    agent_id: str
    status: str = ClaimStatus.VERIFIED.value
    produced_artifacts: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=list)
    verification_obligations: list = field(default_factory=list)  # obligation_ids
    created_at: str = ""


# ---------------------------------------------------------------------------
# Dependency edge (contract-level) + DependencyInstance
# ---------------------------------------------------------------------------

@dataclass
class DependencyEdge:
    source: str               # contract_id or artifact_id
    target: str               # obligation_id or claim_id
    relation_type: str        # CONTRACT->OBLIGATION | CONTRACT->COMPLETION | ARTIFACT->COMPLETION | TASK->TASK | SYMBOL->CONSUMER
    scope: frozenset = field(default_factory=lambda: frozenset({ChangeType.BREAKING}))
    confidence: float = 0.5
    provenance: Provenance = Provenance.STATIC
    granularity: Granularity = Granularity.CONTRACT
    note: str = ""

    def key(self):
        return (self.source, self.target, self.relation_type, self.granularity.value)

    def to_dict(self):
        return {
            "source": self.source, "target": self.target,
            "relation_type": self.relation_type,
            "scope": sorted(s.value if not isinstance(s, str) else s for s in self.scope),
            "confidence": self.confidence,
            "provenance": self.provenance.value if isinstance(self.provenance, Provenance) else self.provenance,
            "granularity": self.granularity.value if isinstance(self.granularity, Granularity) else self.granularity,
            "note": self.note,
        }

    @staticmethod
    def from_dict(d):
        return DependencyEdge(
            source=d["source"], target=d["target"],
            relation_type=d["relation_type"],
            scope=frozenset(ChangeType(s) for s in d.get("scope", ["BREAKING"])),
            confidence=d.get("confidence", 0.5),
            provenance=Provenance(d.get("provenance", "STATIC")),
            granularity=Granularity(d.get("granularity", "CONTRACT")),
            note=d.get("note", ""),
        )


@dataclass
class DependencyInstance:
    """One (producer contract, consumer completion, verify obligation) triple
    that a change case exercises — the unit of v3 experiments."""
    instance_id: str
    repo: str
    producer_task: str
    producer_artifact: str
    producer_contract: str        # contract_id
    consumer_task: str
    consumer_completion: str      # claim_id
    verification_obligation: str  # obligation_id
    change: dict                  # change case descriptor
    ground_truth_relation: str    # the G* edge this instance realizes


@dataclass
class Candidate:
    source: str
    target: str
    relation_type: str
    scope: list
    confidence: float
    reason: str
    method: str                      # static|dynamic|semantic|trace|hybrid
    granularity: Granularity = Granularity.CONTRACT

    def to_dict(self):
        d = asdict(self)
        d["granularity"] = self.granularity.value if isinstance(self.granularity, Granularity) else self.granularity
        return d


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