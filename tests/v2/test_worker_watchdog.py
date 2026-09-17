from autoanalyst.execution.watchdog import budget_violation


def test_budget_violation_detects_timeout_before_memory() -> None:
    assert (
        budget_violation(
            elapsed_seconds=10.001,
            rss_bytes=100,
            max_duration_seconds=10,
            max_memory_bytes=1_000,
        )
        == "runtime_timeout"
    )


def test_budget_violation_detects_memory_limit() -> None:
    assert (
        budget_violation(
            elapsed_seconds=1,
            rss_bytes=1_001,
            max_duration_seconds=10,
            max_memory_bytes=1_000,
        )
        == "memory_budget_exceeded"
    )


def test_budget_violation_allows_values_at_limits() -> None:
    assert (
        budget_violation(
            elapsed_seconds=10,
            rss_bytes=1_000,
            max_duration_seconds=10,
            max_memory_bytes=1_000,
        )
        is None
    )
