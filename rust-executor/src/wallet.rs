use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_sdk::signature::{read_keypair_file, Keypair, Signer};
use std::path::Path;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

pub const EXECUTOR_KEYPAIR_ENV: &str = "PIO_EXECUTOR_KEYPAIR";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WalletStatus {
    pub pubkey: String,
    pub keypair_source: String,
    pub permissions_verified: bool,
}

fn validate_keypair_path(path: &Path) -> Result<()> {
    if !path.is_absolute() {
        anyhow::bail!(
            "{EXECUTOR_KEYPAIR_ENV} must point to an absolute keypair file path"
        );
    }

    let metadata = std::fs::symlink_metadata(path)
        .with_context(|| format!("failed to inspect keypair file: {}", path.display()))?;
    if metadata.file_type().is_symlink() {
        anyhow::bail!("executor keypair path must not be a symlink");
    }
    if !metadata.is_file() {
        anyhow::bail!("executor keypair path must be a regular file");
    }

    #[cfg(unix)]
    {
        let mode = metadata.permissions().mode() & 0o777;
        if mode & 0o077 != 0 {
            anyhow::bail!(
                "executor keypair file permissions are too broad: {:03o}; expected owner-only access",
                mode
            );
        }
    }

    Ok(())
}

pub fn load_executor_keypair(path: impl AsRef<Path>) -> Result<Keypair> {
    let path = path.as_ref();
    validate_keypair_path(path)?;
    read_keypair_file(path).map_err(|error| {
        anyhow::anyhow!(
            "failed to read executor keypair from {}: {error}",
            path.display()
        )
    })
}

pub fn load_executor_keypair_from_env() -> Result<Keypair> {
    let path = std::env::var(EXECUTOR_KEYPAIR_ENV).with_context(|| {
        format!(
            "{EXECUTOR_KEYPAIR_ENV} is required for wallet access; inline private keys are not accepted"
        )
    })?;
    load_executor_keypair(path)
}

pub fn inspect_executor_wallet_from_env() -> Result<WalletStatus> {
    let keypair = load_executor_keypair_from_env()?;
    Ok(WalletStatus {
        pubkey: keypair.pubkey().to_string(),
        keypair_source: EXECUTOR_KEYPAIR_ENV.to_string(),
        permissions_verified: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::signature::Signer;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn temp_keypair_path() -> PathBuf {
        std::env::temp_dir().join(format!("pio-executor-keypair-{}.json", Uuid::new_v4()))
    }

    fn write_keypair(path: &Path, keypair: &Keypair) {
        let bytes = keypair.to_bytes().to_vec();
        std::fs::write(path, serde_json::to_vec(&bytes).unwrap()).unwrap();
    }

    #[test]
    fn relative_keypair_paths_fail_closed() {
        assert!(load_executor_keypair("relative-keypair.json").is_err());
    }

    #[test]
    fn secure_keypair_file_loads_without_exposing_secret_material() {
        let path = temp_keypair_path();
        let expected = Keypair::new();
        write_keypair(&path, &expected);

        #[cfg(unix)]
        {
            let mut permissions = std::fs::metadata(&path).unwrap().permissions();
            permissions.set_mode(0o600);
            std::fs::set_permissions(&path, permissions).unwrap();
        }

        let loaded = load_executor_keypair(&path).unwrap();
        assert_eq!(loaded.pubkey(), expected.pubkey());

        let _ = std::fs::remove_file(path);
    }

    #[cfg(unix)]
    #[test]
    fn group_or_world_access_is_rejected() {
        let path = temp_keypair_path();
        let keypair = Keypair::new();
        write_keypair(&path, &keypair);

        let mut permissions = std::fs::metadata(&path).unwrap().permissions();
        permissions.set_mode(0o640);
        std::fs::set_permissions(&path, permissions).unwrap();

        assert!(load_executor_keypair(&path).is_err());

        let _ = std::fs::remove_file(path);
    }

    #[cfg(unix)]
    #[test]
    fn symlink_keypair_paths_are_rejected() {
        use std::os::unix::fs::symlink;

        let target = temp_keypair_path();
        let link = std::env::temp_dir().join(format!(
            "pio-executor-keypair-link-{}.json",
            Uuid::new_v4()
        ));
        let keypair = Keypair::new();
        write_keypair(&target, &keypair);

        let mut permissions = std::fs::metadata(&target).unwrap().permissions();
        permissions.set_mode(0o600);
        std::fs::set_permissions(&target, permissions).unwrap();
        symlink(&target, &link).unwrap();

        assert!(load_executor_keypair(&link).is_err());

        let _ = std::fs::remove_file(link);
        let _ = std::fs::remove_file(target);
    }
}
