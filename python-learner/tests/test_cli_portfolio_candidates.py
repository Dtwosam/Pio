import pytest

from meteora_learner.cli import _portfolio_candidate_records


def candidate():
    return {
        "rank": 1,
        "pool_address": "pool",
    }


def test_portfolio_candidate_records_accepts_legacy_array():
    records = _portfolio_candidate_records([candidate()])
    assert records == [candidate()]


def test_portfolio_candidate_records_accepts_multi_pool_report():
    raw = {
        "comparison": {
            "candidates": [candidate()],
        }
    }
    records = _portfolio_candidate_records(raw)
    assert records == [candidate()]


def test_portfolio_candidate_records_accepts_direct_candidate_object():
    raw = {"candidates": [candidate()]}
    records = _portfolio_candidate_records(raw)
    assert records == [candidate()]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"comparison": {}},
        {"candidates": "not-a-list"},
        "not-json-object",
        [1],
    ],
)
def test_portfolio_candidate_records_rejects_malformed_shapes(raw):
    with pytest.raises(ValueError):
        _portfolio_candidate_records(raw)
