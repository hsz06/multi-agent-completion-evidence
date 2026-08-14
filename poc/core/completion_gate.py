"""Global completion gate + hidden integration check."""
from __future__ import annotations

from .models import (
    ClaimStatus, ID_API_SCHEMA, ID_FRONTEND_CODE, DOWNSTREAM_CLAIMS,
)

VERIFIED = "VERIFIED"
NOT_READY = "NOT_READY"
FAILED = "FAILED"


def global_completion(world) -> str:
    """Aggregate local completion claims into a global verdict.

    This is exactly the "local aggregation" the PoC attacks: it trusts each
    claim's status without checking whether the underlying evidence is stale.
    """
    statuses = [c.status for c in world.claims.values()]
    if any(s == ClaimStatus.FAILED.value for s in statuses):
        return FAILED
    if any(s in (ClaimStatus.STALE.value, ClaimStatus.PENDING.value) for s in statuses):
        return NOT_READY
    return VERIFIED


def hidden_integration_check(world) -> dict:
    """The ground-truth oracle: does the frontend actually work against the
    *current* API schema? Deterministic field-subset check."""
    schema = world.artifact(ID_API_SCHEMA).content
    schema_fields = set(schema["fields"])
    consumed = set(world.artifact(ID_FRONTEND_CODE).metadata["consumes_fields"])
    missing = sorted(consumed - schema_fields)
    return {
        "result": "FAILED" if missing else "PASSED",
        "consumed_fields": sorted(consumed),
        "schema_fields": sorted(schema_fields),
        "missing_fields": missing,
    }


def is_false_completion(world) -> bool:
    return (global_completion(world) == VERIFIED
            and hidden_integration_check(world)["result"] == "FAILED")


def downstream_status(world) -> dict:
    return {cid: world.claim(cid).status for cid in DOWNSTREAM_CLAIMS
            if cid in world.claims}
