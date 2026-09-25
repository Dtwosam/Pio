use std::collections::BTreeMap;
use std::str::FromStr;

use anchor_client::solana_client::nonblocking::rpc_client::RpcClient;
use anchor_client::solana_client::rpc_client::GetConfirmedSignaturesForAddress2Config;
use anchor_client::solana_sdk::commitment_config::CommitmentConfig;
use anchor_client::solana_sdk::pubkey::Pubkey;
use anchor_client::solana_sdk::signature::Signature;
use anyhow::{Context, Result};
use serde::Serialize;

use crate::events::DecodedDlmmEvent;
use crate::transaction_events::{inspect_transaction_events, TransactionEventSnapshot};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct HistoricalPoolPosition {
    pub position_address: String,
    pub owner: String,
    pub latest_matching_signature: String,
    pub latest_matching_slot: u64,
    pub latest_matching_block_time: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct HistoricalPoolActivityDiscovery {
    pub research_only: bool,
    pub read_only_capture: bool,
    pub pool_address: String,
    pub before_signature: Option<String>,
    pub until_signature: Option<String>,
    pub newest_signature: Option<String>,
    pub signatures_requested: usize,
    pub signatures_scanned: usize,
    pub failed_transactions: usize,
    pub matching_transactions: usize,
    pub positions_found: usize,
    pub has_more: bool,
    pub next_before_signature: Option<String>,
    pub positions: Vec<HistoricalPoolPosition>,
    pub errors: Vec<String>,
}

fn pool_position_owner(
    event: &DecodedDlmmEvent,
    pool_address: &str,
) -> Option<(String, String)> {
    match event {
        DecodedDlmmEvent::AddLiquidity(value)
            if value.lb_pair == pool_address =>
        {
            Some((value.position.clone(), value.from.clone()))
        }
        DecodedDlmmEvent::RemoveLiquidity(value)
            if value.lb_pair == pool_address =>
        {
            Some((value.position.clone(), value.from.clone()))
        }
        DecodedDlmmEvent::Rebalancing(value)
            if value.lb_pair == pool_address =>
        {
            Some((value.position.clone(), value.owner.clone()))
        }
        DecodedDlmmEvent::ClaimFee2(value)
            if value.lb_pair == pool_address =>
        {
            Some((value.position.clone(), value.owner.clone()))
        }
        DecodedDlmmEvent::ClaimReward2(value)
            if value.lb_pair == pool_address =>
        {
            Some((value.position.clone(), value.owner.clone()))
        }
        _ => None,
    }
}

fn matching_positions(
    snapshot: &TransactionEventSnapshot,
    pool_address: &str,
) -> Vec<(String, String)> {
    snapshot
        .events
        .iter()
        .filter_map(|record| pool_position_owner(&record.event, pool_address))
        .collect()
}

pub async fn discover_historical_pool_activity(
    rpc_url: &str,
    pool_address: &str,
    limit: usize,
    before_signature: Option<&str>,
    until_signature: Option<&str>,
) -> Result<HistoricalPoolActivityDiscovery> {
    if limit == 0 || limit > 1_000 {
        anyhow::bail!("limit must be between 1 and 1000");
    }
    let pool = Pubkey::from_str(pool_address).context("invalid pool address")?;
    let before = match before_signature {
        Some(value) if !value.trim().is_empty() => Some(
            Signature::from_str(value.trim())
                .context("invalid before signature")?,
        ),
        _ => None,
    };
    let until = match until_signature {
        Some(value) if !value.trim().is_empty() => Some(
            Signature::from_str(value.trim())
                .context("invalid until signature")?,
        ),
        _ => None,
    };

    let rpc = RpcClient::new(rpc_url.to_string());
    let signatures = rpc
        .get_signatures_for_address_with_config(
            &pool,
            GetConfirmedSignaturesForAddress2Config {
                before,
                until,
                limit: Some(limit),
                commitment: Some(CommitmentConfig::confirmed()),
            },
        )
        .await
        .context("failed to fetch pool transaction signatures")?;

    let newest_signature = signatures
        .first()
        .map(|item| item.signature.clone());
    let next_before_signature = signatures
        .last()
        .map(|item| item.signature.clone());
    let has_more = signatures.len() == limit;
    let mut failed_transactions = 0usize;
    let mut matching_transactions = 0usize;
    let mut positions: BTreeMap<String, HistoricalPoolPosition> =
        BTreeMap::new();
    let mut errors = Vec::new();

    for status in &signatures {
        if status.err.is_some() {
            continue;
        }
        let snapshot = match inspect_transaction_events(
            rpc_url,
            &status.signature,
        )
        .await
        {
            Ok(value) => value,
            Err(error) => {
                failed_transactions += 1;
                if errors.len() < 20 {
                    errors.push(format!(
                        "{}: {}",
                        status.signature,
                        error
                    ));
                }
                continue;
            }
        };
        let matches = matching_positions(&snapshot, pool_address);
        if matches.is_empty() {
            continue;
        }
        matching_transactions += 1;
        for (position_address, owner) in matches {
            positions
                .entry(position_address.clone())
                .or_insert_with(|| HistoricalPoolPosition {
                    position_address,
                    owner,
                    latest_matching_signature: snapshot.signature.clone(),
                    latest_matching_slot: snapshot.slot,
                    latest_matching_block_time: snapshot.block_time,
                });
        }
    }

    Ok(HistoricalPoolActivityDiscovery {
        research_only: true,
        read_only_capture: true,
        pool_address: pool_address.to_string(),
        before_signature: before_signature
            .filter(|value| !value.trim().is_empty())
            .map(str::to_string),
        until_signature: until_signature
            .filter(|value| !value.trim().is_empty())
            .map(str::to_string),
        newest_signature,
        signatures_requested: limit,
        signatures_scanned: signatures.len(),
        failed_transactions,
        matching_transactions,
        positions_found: positions.len(),
        has_more,
        next_before_signature,
        positions: positions.into_values().collect(),
        errors,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{
        AddLiquidityEvent,
        ClaimFee2Event,
        CompositionFeeEvent,
        RemoveLiquidityEvent,
    };
    use crate::transaction_events::TransactionEventRecord;

    fn snapshot(events: Vec<DecodedDlmmEvent>) -> TransactionEventSnapshot {
        TransactionEventSnapshot {
            signature: "sig".to_string(),
            slot: 123,
            block_time: Some(456),
            network_fee_lamports: None,
            compute_units_consumed: None,
            succeeded: Some(true),
            add_requests: Vec::new(),
            rebalance_requests: Vec::new(),
            events: events
                .into_iter()
                .enumerate()
                .map(|(event_index, event)| TransactionEventRecord {
                    event_index,
                    parent_ix_index: 0,
                    event,
                })
                .collect(),
        }
    }

    #[test]
    fn matching_positions_requires_exact_pool_identity() {
        let value = snapshot(vec![
            DecodedDlmmEvent::AddLiquidity(AddLiquidityEvent {
                lb_pair: "pool-a".into(),
                from: "owner-a".into(),
                position: "position-a".into(),
                amount_x: "1".into(),
                amount_y: "2".into(),
                active_bin_id: 0,
            }),
            DecodedDlmmEvent::RemoveLiquidity(RemoveLiquidityEvent {
                lb_pair: "pool-b".into(),
                from: "owner-b".into(),
                position: "position-b".into(),
                amount_x: "1".into(),
                amount_y: "2".into(),
                active_bin_id: 0,
            }),
            DecodedDlmmEvent::ClaimFee2(ClaimFee2Event {
                lb_pair: "pool-a".into(),
                position: "position-c".into(),
                owner: "owner-c".into(),
                fee_x: "1".into(),
                fee_y: "2".into(),
                active_bin_id: 0,
            }),
            DecodedDlmmEvent::CompositionFee(CompositionFeeEvent {
                from: "owner-d".into(),
                bin_id: 0,
                token_x_fee_amount: "1".into(),
                token_y_fee_amount: "2".into(),
                protocol_token_x_fee_amount: "0".into(),
                protocol_token_y_fee_amount: "0".into(),
            }),
        ]);

        assert_eq!(
            matching_positions(&value, "pool-a"),
            vec![
                ("position-a".to_string(), "owner-a".to_string()),
                ("position-c".to_string(), "owner-c".to_string()),
            ]
        );
    }
}
