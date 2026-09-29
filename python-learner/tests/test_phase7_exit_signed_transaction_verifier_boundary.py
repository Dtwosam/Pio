from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = (
    ROOT
    / "rust-executor"
    / "src"
    / "bin"
    / "phase7-exit-signed-transaction-verifier.rs"
)


def test_exit_signed_transaction_verifier_is_read_only():
    source = VERIFIER.read_text(encoding="utf-8")

    assert "exact_signed_exit_transaction_verified" in source
    assert "unsigned_message_matches_signed_message" in source
    assert "signature.verify" in source
    assert "executor_wallet_pubkey" in source
    assert "recent_blockhash" in source

    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert "load_executor_keypair" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "RpcClient" not in source
    assert "execution_store" not in source
    assert "rusqlite" not in source


def test_exit_signed_transaction_verifier_never_mutates_or_rebuilds_message():
    source = VERIFIER.read_text(encoding="utf-8")
    production = source.split("#[cfg(test)]", 1)[0]

    assert "unsigned.message.serialize()" in production
    assert "signed.message.serialize()" in production
    assert "unsigned_message != signed_message" in production

    assert "prepare_unsigned_transaction_with_latest_blockhash" not in production
    assert "recent_blockhash =" not in production
    assert "transaction.signatures[0] =" not in production
