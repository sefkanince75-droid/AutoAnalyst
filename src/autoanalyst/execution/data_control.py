"""Cooperative safe points shared by direct calls and isolated data workers."""

from collections.abc import Callable
from contextvars import ContextVar

JOB_ID: ContextVar[str | None] = ContextVar("data_job_id", default=None)
CHECK: ContextVar[Callable[[bool], None] | None] = ContextVar("data_job_check", default=None)


def checkpoint(*, publication: bool = False) -> None:
    check = CHECK.get()
    if check is not None:
        check(publication)
