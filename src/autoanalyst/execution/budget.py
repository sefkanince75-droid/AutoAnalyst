"""Resource-estimate enforcement for analysis jobs."""

from __future__ import annotations

from ..analyses.contract import ResourceEstimate
from ..domain.errors import ResourceError
from ..domain.plans import ResourceBudget


def enforce_budget(estimate: ResourceEstimate, budget: ResourceBudget) -> None:
    exceeded: list[str] = []
    if estimate.memory_bytes > budget.max_memory_bytes:
        exceeded.append("memory")
    if estimate.disk_bytes > budget.max_disk_bytes:
        exceeded.append("disk")
    if estimate.duration_seconds > budget.max_duration_seconds:
        exceeded.append("duration")
    if estimate.parallelism > budget.max_parallelism:
        exceeded.append("parallelism")
    if exceeded:
        raise ResourceError(
            {
                "reason": "resource_budget_exceeded",
                "dimensions": tuple(exceeded),
                "estimate": {
                    "memory_bytes": estimate.memory_bytes,
                    "disk_bytes": estimate.disk_bytes,
                    "duration_seconds": estimate.duration_seconds,
                    "parallelism": estimate.parallelism,
                },
            }
        )
