from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FINALIZER = (
    ROOT
    / "rust-executor"
    / "src"
    / "bin"
    / "phase7-exit-finalizer.rs"
)


def test_phase7_exit_finalizer_is_non_keypair_and_non_persisting():
    source = FINALIZER.read_text(encoding="utf-8")

    assert "prepare_unsigned_transaction_with_latest_blockhash" in source
    assert "simulate_exact_base64_transaction" in source
    assert "evaluate_final_presign_with" in source
    assert "EXPECTED_WALLET_PUBKEY" in source

    assert "wallet.rs" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "load_executor_keypair" not in source
    assert "inspect_executor_wallet_from_env" not in source
    assert "execution_store" not in source
    assert "record_final_presign" not in source
    assert "controlled-live-submit" not in source
    assert "submission::" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source


def test_phase7_exit_finalizer_keeps_transaction_unsigned_until_later_boundary():
    source = FINALIZER.read_text(encoding="utf-8")

    assert "../blockhash.rs" in source
    assert "../transaction_guard.rs" in source
    assert "../wallet_guard.rs" in source
    assert "../simulation.rs" in source
    assert "../presign.rs" in source

    assert "sign_message" not in source
    assert ".sign(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
