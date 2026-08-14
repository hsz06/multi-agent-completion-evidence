"""VerificationObligation + VerificationPool models."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class VerifierType(str, Enum):
    UNIT_TEST = "UNIT_TEST"
    MODULE_TEST = "MODULE_TEST"
    CONTRACT_TEST = "CONTRACT_TEST"
    INTEGRATION_TEST = "INTEGRATION_TEST"
    STATIC_CHECK = "STATIC_CHECK"


class ObligationSource(str, Enum):
    STATIC = "STATIC"
    DYNAMIC = "DYNAMIC"
    MANUAL = "MANUAL"


@dataclass
class VerificationObligation:
    obligation_id: str          # repo::testfile
    repo: str
    verifier_type: VerifierType
    command: str                # pytest target
    target_tests: str           # the test file
    covered_contracts: list = field(default_factory=list)   # contract_ids (pre-change coverage)
    covered_symbols: list = field(default_factory=list)     # symbols
    covered_files: list = field(default_factory=list)       # source files executed
    estimated_cost: float = 0.0   # pristine runtime seconds
    source: ObligationSource = ObligationSource.DYNAMIC

    def to_dict(self):
        d = asdict(self)
        d["verifier_type"] = self.verifier_type.value
        d["source"] = self.source.value
        return d


@dataclass
class CoverageGap:
    claim: str
    repo: str
    changed_contract: str
    required_contracts: list
    currently_covered: list
    missing_coverage: list
    gap: bool

    def to_dict(self):
        return asdict(self)


@dataclass
class SelectionResult:
    strategy: str
    selected: list              # obligation_ids
    selected_files: list
    covered_missing: list
    threshold: float
    coverage_achieved: float
    estimated_cost: float

    def to_dict(self):
        return asdict(self)