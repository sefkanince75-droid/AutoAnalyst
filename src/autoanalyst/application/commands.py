"""Deterministic commands produce closed proposals; preview/apply stay explicit."""

from __future__ import annotations

import math
import shlex
from dataclasses import dataclass

from ..domain.codec import FrozenDict, encode_typed_label, fingerprint
from ..domain.errors import SchemaError
from ..preparation.operations import affected_column_ids, validate_parameters
from .preparation import PreparationService


@dataclass(frozen=True, slots=True)
class CommandProposal:
    workspace: str
    project_id: str
    dataset_id: str
    base_version_id: str
    head_revision: int
    steps: tuple[FrozenDict, ...]

    @property
    def proposal_hash(self):
        return fingerprint(self)


@dataclass(frozen=True, slots=True)
class Clarification:
    reason: str
    choices: tuple[str, ...] = ()


class CommandService:
    def __init__(self, catalog, store):
        self.catalog, self.store = catalog, store

    def proposal(self, *, project_id, dataset_id, version_id, steps):
        dataset = self.catalog.get_dataset(dataset_id)
        if (
            dataset.project_id != project_id
            or dataset.head_version_id != version_id
            or self.catalog.get_project(project_id).archived_at
        ):
            raise SchemaError({"reason": "command.stale"})
        if not isinstance(steps, (list, tuple)) or not 1 <= len(steps) <= 20:
            raise SchemaError({"reason": "command.invalid_plan"})
        allowed_columns = {
            str(c["column_id"]) for c in self.catalog.list_columns(version_id) if not c["is_system"]
        }
        normalized = []
        for step in steps:
            if (
                not isinstance(step, dict)
                or set(step) != {"operation", "parameters"}
                or step["operation"]
                not in {
                    "filter_rows",
                    "drop_missing_rows",
                    "remove_duplicates",
                    "rename_column",
                    "drop_columns",
                    "cast_column",
                    "fill_missing",
                    "replace_values",
                    "replace_non_finite",
                }
            ):
                raise SchemaError({"reason": "command.invalid_plan"})
            params = validate_parameters(step["operation"], step["parameters"])
            if not set(affected_column_ids(step["operation"], params)).issubset(allowed_columns):
                raise SchemaError({"reason": "column_not_found"})
            normalized.append(FrozenDict({"operation": step["operation"], "parameters": params}))
            if step["operation"] == "drop_columns":
                allowed_columns -= set(params["column_ids"])
        return CommandProposal(
            str(self.catalog.paths.root),
            project_id,
            dataset_id,
            version_id,
            dataset.head_revision,
            tuple(normalized),
        )

    def interpret(self, text, *, project_id, dataset_id, version_id):
        if not isinstance(text, str) or not 1 <= len(text) <= 1000:
            return Clarification("command.syntax")
        try:
            tokens = shlex.split(text)
        except ValueError:
            return Clarification("command.syntax")
        if not tokens:
            return Clarification("command.syntax")
        columns = [dict(c) for c in self.catalog.list_columns(version_id) if not c["is_system"]]
        verbs = {
            "filtre": "filter",
            "filter": "filter",
            "yeniden_adlandir": "rename",
            "rename": "rename",
            "eksikleri_sil": "drop_missing",
            "drop_missing": "drop_missing",
            "tekrarlari_sil": "deduplicate",
            "deduplicate": "deduplicate",
            "sutun_sil": "drop",
            "drop": "drop",
            "doldur": "fill",
            "fill": "fill",
            "degistir": "replace",
            "replace": "replace",
        }
        verb = verbs.get(tokens[0].casefold())
        if verb is None or len(tokens) < 2:
            return Clarification("command.syntax")
        exact = [c for c in columns if c["display_name"] == tokens[1]]
        found = exact or [
            c for c in columns if c["display_name"].casefold() == tokens[1].casefold()
        ]
        if len(found) != 1:
            return Clarification(
                "command.column", tuple(c["display_name"] for c in found or columns)
            )
        column = found[0]
        identifier = str(column["column_id"])
        params: dict[str, object]
        try:
            if verb == "rename" and len(tokens) == 3:
                operation, params = (
                    "rename_column",
                    {"column_id": identifier, "new_name": tokens[2]},
                )
            elif verb in {"drop", "drop_missing", "deduplicate"} and len(tokens) == 2:
                operation = {
                    "drop": "drop_columns",
                    "drop_missing": "drop_missing_rows",
                    "deduplicate": "remove_duplicates",
                }[verb]
                params = {"column_ids": [identifier]}
            elif verb == "fill" and len(tokens) == 3 and tokens[2] in {"mean", "median", "mode"}:
                operation, params = (
                    "fill_missing",
                    {"column_ids": [identifier], "strategy": tokens[2]},
                )
            elif verb == "replace" and len(tokens) == 4:
                operation, params = (
                    "replace_values",
                    {
                        "column_id": identifier,
                        "mapping": [
                            {
                                "from": encode_typed_label(typed_value(tokens[2], column)),
                                "to": encode_typed_label(typed_value(tokens[3], column)),
                            }
                        ],
                    },
                )
            elif verb == "filter" and len(tokens) in {3, 4}:
                operator = {
                    "==": "eq",
                    "=": "eq",
                    "!=": "ne",
                    ">": "gt",
                    ">=": "gte",
                    "<": "lt",
                    "<=": "lte",
                    "contains": "contains_literal",
                    "isnull": "is_null",
                    "notnull": "is_not_null",
                }.get(tokens[2])
                if not operator or (operator in {"is_null", "is_not_null"}) != (len(tokens) == 3):
                    return Clarification("command.syntax")
                operation, params = "filter_rows", {"column_id": identifier, "operator": operator}
                if len(tokens) == 4:
                    params["value"] = (
                        tokens[3]
                        if operator == "contains_literal"
                        else typed_value(tokens[3], column)
                    )
            else:
                return Clarification("command.syntax")
            return self.proposal(
                project_id=project_id,
                dataset_id=dataset_id,
                version_id=version_id,
                steps=[{"operation": operation, "parameters": params}],
            )
        except (ValueError, TypeError, SchemaError):
            return Clarification("command.type")

    def recipe_for(self, proposal):
        dataset = self.catalog.get_dataset(proposal.dataset_id)
        if (
            proposal.workspace != str(self.catalog.paths.root)
            or dataset.project_id != proposal.project_id
            or dataset.head_revision != proposal.head_revision
            or dataset.head_version_id != proposal.base_version_id
        ):
            raise SchemaError({"reason": "command.stale"})
        service = PreparationService(self.catalog, self.store)
        recipe = service.create_recipe(
            project_id=proposal.project_id,
            base_version_id=proposal.base_version_id,
            steps=proposal.steps,
        )
        return recipe

    def preview(self, proposal):
        recipe = self.recipe_for(proposal)
        service = PreparationService(self.catalog, self.store)
        preview = service.preview_recipe(recipe.recipe_id)
        if preview.expected_head_revision != proposal.head_revision:
            raise SchemaError({"reason": "command.stale"})
        return preview

    def apply(self, proposal, preview_id):
        if proposal.workspace != str(self.catalog.paths.root):
            raise SchemaError({"reason": "command.stale"})
        service = PreparationService(self.catalog, self.store)
        preview = service.get_preview(preview_id)
        recipe = service.get_recipe(preview.recipe_id)
        actual = tuple(
            FrozenDict({"operation": s.operation, "parameters": s.parameters})
            for s in recipe.ordered_steps
        )
        if (
            fingerprint(actual) != fingerprint(proposal.steps)
            or preview.expected_head_revision != proposal.head_revision
            or preview.base_version_id != proposal.base_version_id
        ):
            raise SchemaError({"reason": "command.stale"})
        return service.apply_preview(
            preview_id,
            expected_project_id=proposal.project_id,
            expected_base_version_id=proposal.base_version_id,
            expected_recipe_hash=recipe.recipe_hash,
        )


def typed_value(text, column):
    if text.casefold() == "null":
        return None
    physical = str(column["physical_type"]).lower()
    if "bool" in physical:
        if text.casefold() not in {"true", "false"}:
            raise ValueError("boolean required")
        return text.casefold() == "true"
    if "int" in physical:
        return int(text)
    if any(v in physical for v in ("float", "double", "decimal", "numeric")):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("finite number required")
        return value
    if "date" in physical or "time" in physical:
        # Existing engine accepts ISO date/time text and validates it against the column.
        from datetime import datetime

        datetime.fromisoformat(text)
    return text
