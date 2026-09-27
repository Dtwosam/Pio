from __future__ import annotations

import pytest

from meteora_learner.market_paper_intake import _build_from_store


class FakeStore:
    def __init__(self):
        self.calls: list[tuple[str, int, bool]] = []

    def latest_pool_snapshots(self):
        return [
            {"address": "A", "observed_at": "2026-09-27T08:00:00+00:00"},
            {"address": "B", "observed_at": "2026-09-27T08:01:00+00:00"},
            {"address": "C", "observed_at": "2026-09-27T08:02:00+00:00"},
        ]

    def chain_observation_times(self, address, *, limit, ascending):
        self.calls.append((address, limit, ascending))
        rows = {
            "A": ["a3", "a2", "a1"],
            "B": ["b1"],
            "C": ["c3", "c2", "c1"],
        }
        return rows[address][:limit]


def test_intake_marks_only_chain_history_readiness() -> None:
    store = FakeStore()
    report = _build_from_store(
        store,
        minimum_chain_observations=3,
        max_pools=10,
    )

    assert report.research_only is True
    assert report.paper_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.pools_seen == 3
    assert report.pools_considered == 3
    assert report.pools_ready == 2
    assert [item.pool_address for item in report.pools] == ["A", "B", "C"]
    assert [item.ready_for_candidate_cycle for item in report.pools] == [
        True,
        False,
        True,
    ]
    assert report.pools[0].latest_chain_observed_at == "a3"
    assert store.calls == [
        ("A", 3, False),
        ("B", 3, False),
        ("C", 3, False),
    ]


def test_intake_is_bounded_without_ranking() -> None:
    store = FakeStore()
    report = _build_from_store(
        store,
        minimum_chain_observations=2,
        max_pools=2,
    )

    assert report.pools_seen == 3
    assert report.pools_considered == 2
    assert [item.pool_address for item in report.pools] == ["A", "B"]
    assert store.calls == [
        ("A", 2, False),
        ("B", 2, False),
    ]


@pytest.mark.parametrize(
    ("minimum_chain_observations", "max_pools"),
    [
        (1, 10),
        (2, 0),
    ],
)
def test_intake_rejects_unsafe_bounds(
    minimum_chain_observations: int,
    max_pools: int,
) -> None:
    with pytest.raises(ValueError):
        _build_from_store(
            FakeStore(),
            minimum_chain_observations=minimum_chain_observations,
            max_pools=max_pools,
        )
