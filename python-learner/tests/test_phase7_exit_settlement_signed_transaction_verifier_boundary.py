from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = (
    ROOT
    / "rust-executor"
    / "src"
    / "bin"
    / "phase7-exit-settlement-signed-transaction-verifier.rs"
)


def _production_source() -> str:
    source = VERIFIER.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def test_settlement_signed_transaction_verifier_is_read_only():
    source = _production_source()

    assert "unsigned.message.serialize()" in source
    assert "signed.message.serialize()" in source
    assert "signature.verify" in source
    assert (
        "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_SINGLE_EXECUTION_REQUEST_V1"
        in source
    )
    assert "exact_signed_settlement_transaction_verified: true" in source

    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "load_executor_keypair" not in source
    assert "Keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "rusqlite" not in source
    assert "execution_store" not in source


def test_settlement_signed_transaction_verifier_never_mutates_message():
    source = _production_source()

    assert "prepare_unsigned_transaction_with_latest_blockhash" not in source
    assert "recent_blockhash =" not in source
    assert "transaction.signatures[0] =" not in source
    assert "signed.signatures[0] =" not in source


def test_settlement_verifier_is_typed_separately_from_liquidity_exit():
    source = _production_source()

    assert (
        'const ARTIFACT_TYPE: &str =\n'
        '    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_SINGLE_EXECUTION_REQUEST_V1";'
        in source
    )
    assert (
        "PHASE7_CONTROLLED_LIVE_EXIT_SINGLE_EXECUTION_REQUEST_V1"
        not in source
    )
    assert "final_settlement_transaction_base64" in source
    assert "destination_config_sha256" in source
