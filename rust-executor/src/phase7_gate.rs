use anyhow::{Context, Result};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

const PHASE_NAME: &str = "PHASE7";
const EVIDENCE_TYPE: &str = "PHASE7_PROMOTION_V1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Phase7PromotionGateReport {
    pub accepted: bool,
    pub reason: String,
    pub phase_name: String,
    pub evidence_type: Option<String>,
    pub promoted_at: Option<String>,
    pub qualified: bool,
    pub evidence_promotion_ready: bool,
    pub evidence_phase6_promoted: bool,
    pub ledger_clean: bool,
    pub confirmed_receipts: u64,
    pub failed_receipts: u64,
    pub closed_positions: u64,
    pub open_positions: u64,
    pub distinct_closed_pools: u64,
    pub valued_closed_positions: u64,
    pub labeled_closed_positions: u64,
}

fn bool_field(value: &Value, key: &str) -> bool {
    value.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn u64_field(value: &Value, key: &str) -> Option<u64> {
    value.get(key).and_then(Value::as_u64)
}

fn criteria_u64(value: &Value, key: &str) -> Option<u64> {
    value
        .get("criteria")
        .and_then(|item| item.get(key))
        .and_then(Value::as_u64)
}

pub fn verify_phase7_promotion_database(
    database_path: &Path,
) -> Result<Phase7PromotionGateReport> {
    if !database_path.is_absolute() {
        anyhow::bail!("Phase 7 promotion database path must be absolute");
    }
    let conn = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| {
        format!(
            "failed to open Phase 7 promotion database read-only: {}",
            database_path.display()
        )
    })?;

    let row = conn.query_row(
        r#"
        SELECT promoted_at, evidence_type, qualified, evidence_json
        FROM phase_promotion_evidence
        WHERE phase_name = ?1
        LIMIT 1
        "#,
        [PHASE_NAME],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
            ))
        },
    );

    let (promoted_at, evidence_type, qualified_raw, evidence_json) =
        match row {
            Ok(value) => value,
            Err(rusqlite::Error::QueryReturnedNoRows) => {
                return Ok(Phase7PromotionGateReport {
                    accepted: false,
                    reason: "phase7_promotion_evidence_missing".into(),
                    phase_name: PHASE_NAME.into(),
                    evidence_type: None,
                    promoted_at: None,
                    qualified: false,
                    evidence_promotion_ready: false,
                    evidence_phase6_promoted: false,
                    ledger_clean: false,
                    confirmed_receipts: 0,
                    failed_receipts: 0,
                    closed_positions: 0,
                    open_positions: 0,
                    distinct_closed_pools: 0,
                    valued_closed_positions: 0,
                    labeled_closed_positions: 0,
                });
            }
            Err(error) => {
                return Err(error)
                    .context("phase_promotion_evidence table is unavailable");
            }
        };

    let evidence: Value = serde_json::from_str(&evidence_json)
        .context("Phase 7 evidence_json is invalid JSON")?;
    let qualified = qualified_raw == 1;
    let promotion_ready = bool_field(&evidence, "promotion_ready");
    let phase6_promoted = bool_field(&evidence, "phase6_promoted");
    let ledger_clean = evidence
        .get("ledger_audit")
        .map(|audit| bool_field(audit, "clean"))
        .unwrap_or(false);

    let confirmed_receipts =
        u64_field(&evidence, "confirmed_receipts").unwrap_or(u64::MAX);
    let failed_receipts =
        u64_field(&evidence, "failed_receipts").unwrap_or(u64::MAX);
    let closed_positions =
        u64_field(&evidence, "closed_positions").unwrap_or(u64::MAX);
    let open_positions =
        u64_field(&evidence, "open_positions").unwrap_or(u64::MAX);
    let distinct_closed_pools =
        u64_field(&evidence, "distinct_closed_pools").unwrap_or(u64::MAX);
    let valued_closed_positions =
        u64_field(&evidence, "valued_closed_positions").unwrap_or(u64::MAX);
    let labeled_closed_positions =
        u64_field(&evidence, "labeled_closed_positions").unwrap_or(u64::MAX);

    let min_closed =
        criteria_u64(&evidence, "min_closed_positions");
    let min_pools =
        criteria_u64(&evidence, "min_distinct_pools");
    let min_receipts =
        criteria_u64(&evidence, "min_confirmed_receipts");
    let max_failed =
        criteria_u64(&evidence, "max_failed_receipts");
    let max_open =
        criteria_u64(&evidence, "max_open_positions_at_validation");

    let reason = if evidence_type != EVIDENCE_TYPE {
        "phase7_evidence_type_mismatch"
    } else if !qualified {
        "phase7_evidence_not_qualified"
    } else if !promotion_ready {
        "phase7_evidence_not_promotion_ready"
    } else if !phase6_promoted {
        "phase7_evidence_missing_phase6_promotion"
    } else if !ledger_clean {
        "phase7_live_ledger_not_clean"
    } else if min_closed.is_none()
        || closed_positions < min_closed.unwrap()
    {
        "phase7_closed_positions_below_criteria"
    } else if min_pools.is_none()
        || distinct_closed_pools < min_pools.unwrap()
    {
        "phase7_distinct_pools_below_criteria"
    } else if min_receipts.is_none()
        || confirmed_receipts < min_receipts.unwrap()
    {
        "phase7_confirmed_receipts_below_criteria"
    } else if max_failed.is_none()
        || failed_receipts > max_failed.unwrap()
    {
        "phase7_failed_receipts_exceed_criteria"
    } else if max_open.is_none()
        || open_positions > max_open.unwrap()
    {
        "phase7_open_positions_exceed_criteria"
    } else if valued_closed_positions != closed_positions {
        "phase7_closed_positions_not_fully_valued"
    } else if labeled_closed_positions != closed_positions {
        "phase7_closed_positions_not_fully_labeled"
    } else {
        "approved"
    };

    Ok(Phase7PromotionGateReport {
        accepted: reason == "approved",
        reason: reason.into(),
        phase_name: PHASE_NAME.into(),
        evidence_type: Some(evidence_type),
        promoted_at: Some(promoted_at),
        qualified,
        evidence_promotion_ready: promotion_ready,
        evidence_phase6_promoted: phase6_promoted,
        ledger_clean,
        confirmed_receipts,
        failed_receipts,
        closed_positions,
        open_positions,
        distinct_closed_pools,
        valued_closed_positions,
        labeled_closed_positions,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;
    use serde_json::json;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!(
            "pio-phase7-gate-{}.db",
            Uuid::new_v4()
        ))
    }

    fn ready_evidence() -> Value {
        json!({
            "promotion_ready": true,
            "phase6_promoted": true,
            "ledger_audit": {"clean": true},
            "confirmed_receipts": 6,
            "failed_receipts": 0,
            "closed_positions": 3,
            "open_positions": 0,
            "distinct_closed_pools": 2,
            "valued_closed_positions": 3,
            "labeled_closed_positions": 3,
            "criteria": {
                "min_closed_positions": 3,
                "min_distinct_pools": 2,
                "min_confirmed_receipts": 6,
                "max_failed_receipts": 0,
                "max_open_positions_at_validation": 0
            }
        })
    }

    fn seed(path: &Path, evidence: Value) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            r#"
            CREATE TABLE phase_promotion_evidence (
                phase_name TEXT PRIMARY KEY,
                promoted_at TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            );
            "#,
        )
        .unwrap();
        conn.execute(
            r#"
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type,
                qualified, evidence_json
            ) VALUES (?1, ?2, ?3, 1, ?4)
            "#,
            params![
                PHASE_NAME,
                "2026-09-23T15:00:00+00:00",
                EVIDENCE_TYPE,
                serde_json::to_string(&evidence).unwrap(),
            ],
        )
        .unwrap();
    }

    #[test]
    fn qualified_phase7_evidence_passes() {
        let path = db_path();
        seed(&path, ready_evidence());

        let report = verify_phase7_promotion_database(&path).unwrap();

        assert!(report.accepted);
        assert_eq!(report.reason, "approved");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn incomplete_live_ledger_is_rejected() {
        let path = db_path();
        let mut evidence = ready_evidence();
        evidence["ledger_audit"]["clean"] = json!(false);
        seed(&path, evidence);

        let report = verify_phase7_promotion_database(&path).unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "phase7_live_ledger_not_clean");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn failed_receipt_is_rejected() {
        let path = db_path();
        let mut evidence = ready_evidence();
        evidence["failed_receipts"] = json!(1);
        seed(&path, evidence);

        let report = verify_phase7_promotion_database(&path).unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "phase7_failed_receipts_exceed_criteria"
        );
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn profitability_is_not_part_of_phase7_gate() {
        let evidence = ready_evidence();
        assert!(evidence.get("realized_pnl_quote").is_none());
    }
}
