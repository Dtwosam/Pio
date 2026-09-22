from __future__ import annotations

from .models import Candidate, ScoredCandidate

MODEL_VERSION = "baseline-v0"


def score_candidate(c: Candidate, risk_lambda: float = 1.5) -> ScoredCandidate:
    """First transparent benchmark. ML must eventually beat this out of sample."""
    expected_net = (
        c.fee_yield_24h
        + 0.15 * c.volume_acceleration
        + 0.05 * c.liquidity_score
        - c.estimated_cost_pct
        - 0.35 * c.volatility_24h
    )
    downside = max(0.0, 0.55 * c.volatility_24h + c.estimated_cost_pct)
    score = expected_net - risk_lambda * downside

    return ScoredCandidate(
        **c.model_dump(),
        expected_net_return=expected_net,
        expected_downside=downside,
        score=score,
        model_version=MODEL_VERSION,
    )
