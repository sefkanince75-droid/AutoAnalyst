"""Persistence helpers for final-holdout access discipline."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from uuid import uuid4

from ..domain.codec import fingerprint, utc_now
from ..domain.errors import SchemaError
from .sqlite import SQLiteCatalog

_RETRYABLE_FINAL_STATES = {"failed", "cancelled"}
_ACTIVE_FINAL_STATES = {"pending", "running"}


def holdout_selection_hash(training_run_id: str, provenance: Mapping[str, object]) -> str:
    """Fingerprint every decision that must be frozen before final-test access."""
    recommendation = provenance.get("recommended_model")
    model_meta = provenance.get("model_artifact")
    split_meta = provenance.get("split_artifact")
    if (
        not isinstance(recommendation, Mapping)
        or not isinstance(model_meta, Mapping)
        or not isinstance(split_meta, Mapping)
    ):
        raise SchemaError(
            {"reason": "holdout_selection_metadata_missing", "training_run_id": training_run_id}
        )
    required_recommendation = {"model_id", "threshold"}
    if not required_recommendation.issubset(recommendation):
        raise SchemaError(
            {"reason": "holdout_recommendation_incomplete", "training_run_id": training_run_id}
        )
    for metadata, kind in ((model_meta, "model"), (split_meta, "split")):
        if "sha256" not in metadata:
            raise SchemaError(
                {
                    "reason": "holdout_artifact_checksum_missing",
                    "training_run_id": training_run_id,
                    "artifact_kind": kind,
                }
            )
    return fingerprint(
        {
            "training_run_id": training_run_id,
            "training_spec_hash": provenance.get("spec_hash"),
            "input_version_id": provenance.get("input_version_id"),
            "target_column_id": provenance.get("target_column_id"),
            "positive_label": provenance.get("positive_label"),
            "negative_label": provenance.get("negative_label"),
            "feature_column_ids": provenance.get("feature_column_ids"),
            "feature_semantic_types": provenance.get("feature_semantic_types"),
            "feature_physical_families": provenance.get("feature_physical_families"),
            "split_policy": provenance.get("split_policy"),
            "model_id": recommendation["model_id"],
            "threshold": recommendation["threshold"],
            "model_sha256": model_meta["sha256"],
            "split_sha256": split_meta["sha256"],
            **(
                {
                    "model_task": provenance.get("model_task"),
                    "class_labels": provenance.get("class_labels"),
                }
                if "model_task" in provenance
                else {}
            ),
        }
    )


class BinaryStore:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.catalog = catalog

    def start_holdout_access(
        self, training_run_id: str, final_run_id: str, selection_hash: str
    ) -> dict[str, object]:
        """Persist the irreversible final-selection lock before test data is read.

        A retry may take ownership only after the previous final run failed or was
        cancelled and only when the exact selection hash is unchanged. A second
        active run, a completed result, or a different selection is rejected.
        """
        with self.catalog.transaction() as connection:
            _backfill_source_locks(connection)
            source_identities = _protected_source_identities(connection, training_run_id)
            existing = connection.execute(
                "SELECT * FROM holdout_locks WHERE training_run_id = ?", (training_run_id,)
            ).fetchone()
            if existing is not None:
                if existing["selection_hash"] != selection_hash:
                    raise SchemaError(
                        {"reason": "holdout_selection_changed", "training_run_id": training_run_id}
                    )
                if existing["status"] == "reported":
                    raise SchemaError(
                        {"reason": "holdout_already_reported", "training_run_id": training_run_id}
                    )
                if existing["final_run_id"] == final_run_id:
                    return dict(existing)

                previous = connection.execute(
                    "SELECT status, result_id FROM analysis_runs WHERE run_id = ?",
                    (existing["final_run_id"],),
                ).fetchone()
                if previous is None:
                    raise SchemaError(
                        {
                            "reason": "holdout_previous_final_missing",
                            "training_run_id": training_run_id,
                        }
                    )
                previous_status = str(previous["status"])
                if previous_status in _ACTIVE_FINAL_STATES:
                    raise SchemaError(
                        {
                            "reason": "holdout_final_already_active",
                            "training_run_id": training_run_id,
                            "final_run_id": existing["final_run_id"],
                        }
                    )
                if previous_status not in _RETRYABLE_FINAL_STATES:
                    raise SchemaError(
                        {
                            "reason": "holdout_final_result_exists",
                            "training_run_id": training_run_id,
                            "final_run_id": existing["final_run_id"],
                        }
                    )
                connection.execute(
                    "UPDATE holdout_locks SET final_run_id = ? WHERE training_run_id = ?",
                    (final_run_id, training_run_id),
                )
                updated = connection.execute(
                    "SELECT * FROM holdout_locks WHERE training_run_id = ?", (training_run_id,)
                ).fetchone()
                assert updated is not None
                return dict(updated)

            lock_id = str(uuid4())
            placeholders = ",".join("?" for _ in source_identities)
            exposed = connection.execute(
                f"""SELECT source_identity, training_run_id
                    FROM holdout_source_locks
                    JOIN holdout_locks USING (lock_id)
                    WHERE source_identity IN ({placeholders})
                    LIMIT 1""",
                source_identities,
            ).fetchone()
            if exposed is not None:
                raise SchemaError(
                    {
                        "reason": "holdout_source_already_exposed",
                        "training_run_id": training_run_id,
                        "previous_training_run_id": str(exposed["training_run_id"]),
                    }
                )
            started = utc_now().isoformat().replace("+00:00", "Z")
            connection.execute(
                """INSERT INTO holdout_locks
                   (lock_id, training_run_id, selection_hash, status, final_run_id, access_started_at)
                   VALUES (?, ?, ?, 'access_started', ?, ?)""",
                (lock_id, training_run_id, selection_hash, final_run_id, started),
            )
            connection.executemany(
                "INSERT INTO holdout_source_locks (source_identity, lock_id) VALUES (?, ?)",
                ((source_identity, lock_id) for source_identity in source_identities),
            )
            return {
                "lock_id": lock_id,
                "training_run_id": training_run_id,
                "selection_hash": selection_hash,
                "status": "access_started",
                "final_run_id": final_run_id,
                "access_started_at": started,
            }

    def mark_reported(self, training_run_id: str, final_run_id: str) -> None:
        """Legacy helper for explicit callers; coordinator publication is atomic in RunStore."""
        with self.catalog.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM holdout_locks WHERE training_run_id = ?", (training_run_id,)
            ).fetchone()
            if row is None:
                raise SchemaError(
                    {"reason": "holdout_lock_missing", "training_run_id": training_run_id}
                )
            if row["final_run_id"] != final_run_id:
                raise SchemaError(
                    {"reason": "holdout_final_run_mismatch", "training_run_id": training_run_id}
                )
            if row["status"] == "reported":
                return
            cursor = connection.execute(
                "UPDATE holdout_locks SET status = 'reported' WHERE training_run_id = ? AND status = 'access_started'",
                (training_run_id,),
            )
            if cursor.rowcount != 1:
                raise SchemaError(
                    {"reason": "holdout_status_conflict", "training_run_id": training_run_id}
                )

    def get_holdout_lock(self, training_run_id: str) -> dict[str, object] | None:
        with self.catalog.connection() as connection:
            row = connection.execute(
                "SELECT * FROM holdout_locks WHERE training_run_id = ?", (training_run_id,)
            ).fetchone()
        return dict(row) if row is not None else None


def _backfill_source_locks(connection: sqlite3.Connection) -> None:
    """Protect locks created before source-identity persistence was introduced."""
    locks = tuple(
        connection.execute(
            "SELECT lock_id, training_run_id FROM holdout_locks ORDER BY access_started_at, lock_id"
        )
    )
    for lock in locks:
        for source_identity in _protected_source_identities(
            connection, str(lock["training_run_id"])
        ):
            connection.execute(
                "INSERT OR IGNORE INTO holdout_source_locks (source_identity, lock_id) VALUES (?, ?)",
                (source_identity, str(lock["lock_id"])),
            )


def _protected_source_identities(
    connection: sqlite3.Connection, training_run_id: str
) -> tuple[str, ...]:
    rows = tuple(
        connection.execute(
            """SELECT DISTINCT source.raw_sha256, source.format, imported.parse_contract
               FROM analysis_runs AS run
               JOIN analysis_specs AS spec ON spec.spec_id = run.spec_id
               JOIN version_inputs AS selected ON selected.version_id = spec.input_version_id
               JOIN dataset_sources AS source ON source.source_id = selected.source_id
               JOIN version_inputs AS origin ON origin.source_id = source.source_id
               JOIN dataset_versions AS imported ON imported.version_id = origin.version_id
               WHERE run.run_id = ? AND imported.kind = 'imported'
               ORDER BY source.raw_sha256, imported.parse_contract""",
            (training_run_id,),
        )
    )
    if not rows:
        raise SchemaError(
            {"reason": "holdout_source_identity_missing", "training_run_id": training_run_id}
        )
    identities: set[str] = set()
    for row in rows:
        worksheet = None
        if str(row["format"]) == "xlsx":
            try:
                contract = json.loads(str(row["parse_contract"]))
                worksheet = contract["sheet_name"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise SchemaError(
                    {
                        "reason": "holdout_source_identity_invalid",
                        "training_run_id": training_run_id,
                    }
                ) from exc
            if not isinstance(worksheet, str) or not worksheet:
                raise SchemaError(
                    {
                        "reason": "holdout_source_identity_invalid",
                        "training_run_id": training_run_id,
                    }
                )
        identities.add(fingerprint({"raw_sha256": str(row["raw_sha256"]), "worksheet": worksheet}))
    return tuple(sorted(identities))
