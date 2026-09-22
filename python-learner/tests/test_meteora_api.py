import pytest

from meteora_learner.meteora_api import MeteoraDataAPI


def test_sort_by_requires_direction():
    api = MeteoraDataAPI(max_retries=0)
    try:
        with pytest.raises(ValueError):
            api.pools(sort_by="tvl")
    finally:
        api.close()
