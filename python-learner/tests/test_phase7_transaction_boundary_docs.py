from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / 'deploy' / 'phase7-controlled-live-transaction-boundary.md'

REVIEWED_PHASE7_TOOL_BLOBS = {
    'deploy/tools/check_phase7_controlled_live_evidence_status.py':
        '77aa894be5d3e04534e521d159fa4743e759ef06',
    'deploy/tools/build_phase7_controlled_live_evidence_plan.py':
        '3afb813bc08a57bc0a9d1a8d2df4439e4bfa8824',
    'deploy/tools/check_phase7_controlled_live_input_preflight.py':
        '3696ac1b747cedc6bd3e2c10c84c28ad81cfa711',
    'deploy/tools/build_phase7_controlled_live_signed_authorization.py':
        'c18c16dae3f65491ce4a660ec54f01dfe53a1cfa',
    'deploy/tools/check_phase7_controlled_live_authorization_readiness.py':
        '0df067b15e349736aee022c7404c5417c1c1b8b5',
    'deploy/tools/check_phase7_controlled_live_presubmit_evidence.py':
        '180980842a673354b024db78195b6bfe0209ab6f',
    'deploy/tools/build_phase7_controlled_live_transaction_request.py':
        '068173f916999bf0bef5c61dcf6cc996b8811804',
    'deploy/tools/build_phase7_controlled_live_transaction_signed_authorization.py':
        '4889d419f559503add46282fe40bb2d71ccd5ee0',
    'deploy/tools/check_phase7_controlled_live_transaction_execution_readiness.py':
        '61bb12102b2fb03593dc4bcb9d6130882a9809de',
}


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f'blob {len(payload)}\0'.encode()
    return hashlib.sha1(header + payload).hexdigest()


def test_phase7_terminal_boundary_pins_exact_reviewed_tools():
    text = RUNBOOK.read_text(encoding='utf-8')

    for relative, expected_blob in REVIEWED_PHASE7_TOOL_BLOBS.items():
        path = ROOT / relative
        assert path.is_file(), relative
        assert _git_blob_sha(path) == expected_blob, relative
        assert f'`{Path(relative).name}`' in text
        assert f'`{expected_blob}`' in text


def test_phase7_terminal_boundary_is_explicitly_non_executing():
    text = RUNBOOK.read_text(encoding='utf-8')

    assert '## Stop boundary' in text
    assert '`transaction_signing_authorized=false`' in text
    assert '`transaction_submission_authorized=false`' in text
    assert '`live_capital_authorized=false`' in text
    assert 'No tool in this reviewed chain is a transaction signer or submitter.' in text
    assert 'load an executor private key' in text
    assert 'call Solana `sendTransaction`' in text
    assert 'retry or resubmit' in text
    assert 'persist Phase 7 promotion' in text


def test_phase7_terminal_boundary_has_clean_markdown_backticks():
    text = RUNBOOK.read_text(encoding='utf-8')

    assert r'\`' not in text
