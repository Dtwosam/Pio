from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OBSERVER = (
    ROOT
    / "rust-executor"
    / "src"
    / "bin"
    / "phase7-exit-confirmation-observer.rs"
)


def _production_source() -> str:
    source = OBSERVER.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def test_exit_confirmation_observer_is_read_only():
    source = _production_source()

    assert "get_signature_status_with_commitment_and_history" in source
    assert "CommitmentConfig::confirmed()" in source
    assert "search_transaction_history: true" in source
    assert "observation_only: true" in source
    assert "automatic_retry_performed: false" in source
    assert "transaction_submission_attempted: false" in source

    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "load_executor_keypair" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "rusqlite" not in source
    assert "execution_store" not in source
    assert "phase7_exit_submission_journal" not in source


def test_exit_confirmation_observer_never_mutates_chain_or_local_state():
    source = _production_source()

    assert "RpcClient::new" in source
    assert ".get_signature_status_with_commitment_and_history(" in source

    assert ".execute(" not in source
    assert ".transaction(" not in source
    assert "std::fs::write" not in source
    assert "OpenOptions" not in source
    assert "remove_file" not in source
    assert "create_dir" not in source
