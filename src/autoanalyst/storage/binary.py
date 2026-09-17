"""Persistence helpers for final-holdout access discipline."""

from __future__ import annotations

from uuid import uuid4

from ..domain.codec import utc_now
from ..domain.errors import SchemaError
from .sqlite import SQLiteCatalog

_RETRYABLE_FINAL_STATES = {"failed", "cancelled"}
_ACTIVE_FINAL_STATES = {"pending", "running"}


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
