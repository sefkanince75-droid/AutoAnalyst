"""Persistence helpers for final-holdout access discipline."""

from __future__ import annotations

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
    if not isinstance(recommendation, Mapping) or not isinstance(model_meta, Mapping) or not isinstance(
        split_meta, Mapping
    ):
        raise SchemaError({"reason": "holdout_selection_metadata_missing", "training_run_id": training_run_id})
    required_recommendation = {"model_id", "threshold"}
    if not required_recommendation.issubset(recommendation):
        raise SchemaError({"reason": "holdout_recommendation_incomplete", "training_run_id": training_run_id})
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
                        {"reason": "holdout_previous_final_missing", "training_run_id": training_run_id}
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
            started = utc_now().isoformat().replace("+00:00", "Z")
            connection.execute(
                """INSERT INTO holdout_locks
                   (lock_id, training_run_id, selection_hash, status, final_run_id, access_started_at)
                   VALUES (?, ?, ?, 'access_started', ?, ?)""",
                (lock_id, training_run_id, selection_hash, final_run_id, started),
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
