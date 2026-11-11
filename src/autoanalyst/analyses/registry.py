"""Closed V2.0 analysis registry; dynamic plugin discovery is intentionally absent."""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.errors import DependencyError, SchemaError
from ..domain.plans import AnalysisModuleId
from .contract import AnalysisModule

ALLOWED_MODULE_IDS = frozenset(item.value for item in AnalysisModuleId)


class AnalysisRegistry:
    def __init__(self, modules: Iterable[AnalysisModule] = ()) -> None:
        registered: dict[str, AnalysisModule] = {}
        for module in modules:
            description = module.describe()
            try:
                module_id = AnalysisModuleId(description.module_id).value
            except ValueError as exc:
                raise SchemaError(
                    {"module_id": str(description.module_id), "reason": "module_not_allowed"}
                ) from exc
            if module_id in registered:
                raise SchemaError({"module_id": module_id, "reason": "duplicate_module"})
            registered[module_id] = module
        self._modules = registered

    @property
    def module_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._modules))

    def get(self, module_id: AnalysisModuleId | str) -> AnalysisModule:
        try:
            normalized = AnalysisModuleId(module_id).value
        except ValueError as exc:
            raise SchemaError(
                {"module_id": str(module_id), "reason": "module_not_allowed"}
            ) from exc
        try:
            return self._modules[normalized]
        except KeyError as exc:
            raise DependencyError(
                {"module_id": normalized, "reason": "module_not_registered"}
            ) from exc
