"""Invalidation strategies compared in Experiment 3."""
from __future__ import annotations


class Strategy:
    NONE = "none"                        # baseline: never invalidate
    ALL_DOWNSTREAM = "all_downstream"    # any change -> every other claim STALE
    STATIC = "static"                    # dependency graph, ignore change type
    CHANGE_AWARE = "change_aware"        # dependency graph + edge scope vs change type


ALL_STRATEGIES = (Strategy.ALL_DOWNSTREAM, Strategy.STATIC, Strategy.CHANGE_AWARE)


def select_targets(world, artifact, change_type) -> list[str]:
    """Which claims should go STALE when `artifact` changes."""
    strategy = world.strategy
    if strategy == Strategy.NONE:
        return []
    if strategy == Strategy.ALL_DOWNSTREAM:
        producer_agent = world.tasks[artifact.producer_task].owner_agent
        return [cid for cid, c in world.claims.items() if c.agent_id != producer_agent]
    if strategy == Strategy.STATIC:
        return world.graph.downstream_claims(artifact.artifact_id, change_aware=False)
    if strategy == Strategy.CHANGE_AWARE:
        return world.graph.downstream_claims(artifact.artifact_id, change_type,
                                             change_aware=True)
    raise ValueError(f"unknown strategy {strategy!r}")
