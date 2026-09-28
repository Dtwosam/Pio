# Pio

Adaptive Meteora DLMM liquidity bot with a Rust execution/risk layer and a Python data/learning layer.

## Read first

- docs/SOURCE_OF_TRUTH.md
- docs/BUILD_PHASES.md
- docs/ARCHITECTURE.md
- docs/DATA_PIPELINE.md
- docs/DATA_SCHEMA.md
- docs/SIMULATOR_DESIGN.md
- docs/PHASE2_VALIDATION_RUNBOOK.md
- docs/PHASE3_POLICY.md
- docs/PHASE5_VALIDATION_RUNBOOK.md
- docs/PHASE6_EXECUTION_RUNBOOK.md
- docs/PHASE7_CONTROLLED_LIVE_RUNBOOK.md
- docs/PHASE9_RESEARCH_RUNBOOK.md
- deploy/manual-market-paper-production-preflight.md
- deploy/manual-market-paper-conflict-evidence.md
- deploy/manual-market-paper-preservation-review.md
- deploy/manual-market-paper-handoff.md
- deploy/manual-market-paper-deployment-plan.md
- deploy/manual-market-paper-deployment-gate.md
- deploy/manual-market-paper-mutation-review.md
- deploy/manual-market-paper-preserved-backup-capture.md
- deploy/manual-market-paper-preserved-authorization-review.md
- deploy/manual-market-paper-preserved-human-authorization-request.md
- deploy/manual-market-paper-preserved-signed-human-authorization.md
- deploy/manual-market-paper-preserved-execution-precheck.md
- deploy/manual-market-paper-preserved-file-writer.md
- deploy/manual-market-paper-preserved-post-mutation-audit.md
- deploy/manual-market-paper-one-cycle-proof.md
- deploy/manual-market-paper-phase5-evidence-status.md
- deploy/manual-market-paper-phase5-evidence-collection-loop.md
- deploy/phase6-prelive-promotion-boundary.md
- deploy/market-paper-manual-activation.md

## Current status

- Phase 0: complete
- Phase 1: implemented; extended live validation pending
- Phase 2: standard-SPL simulator implementation complete; real promotion evidence pending
- Phase 3: deterministic policy + persisted validation/promotion workflow implemented; real promotion evidence pending
- Phase 4: reproducible no-lookahead ML challenger workflow implemented; not promoted
- Phase 5: implementation complete; persisted real PAPER promotion evidence pending
- Phase 6: implementation + persistent pre-live validation workflow complete; real promotion evidence pending; default builds cannot submit
- Phase 7: controlled-live safety/validation framework implemented; real promotion evidence pending
- Phase 8: continuous-learning/champion monitoring workflow implemented; real promotion evidence pending
- Phase 9: advanced research implementation complete behind a non-actionable evidence boundary; qualified research-bundle evidence pending
- Default mode: PAPER
- Live submit: available only in a non-default `live-submit` build, additionally runtime-disabled by default and gated by Phase 5/6 promotion plus controlled-live authorization

### Current production continuation

The current production continuation is evidence-first and read-only:

- the exact production detector/watcher sources and unit contracts are tracked and regression-tested;
- never pull, checkout, reset, restore, or clean over `/opt/pio` because production may contain important local changes;
- the manual market/PAPER readiness path preflights only the exact Phase-2 shared prerequisites (`research_store.py` and `storage.py`), the separately guarded state reader, and the locked runtime-only PAPER overlay;
- if that readiness preflight is blocked, `deploy/tools/collect_manual_market_paper_conflict_evidence.py` records only exact current/base/target hashes, file sizes, service/cursor state, and categorized Git-read failures; the normal handoff/plan/gate path stops until those production-local bytes are reviewed;
- after exact conflict hashes are sealed, `deploy/tools/analyze_manual_market_paper_conflict_reconciliation.py` performs a temp-only three-way merge analysis of the two production-local conflicts against the reviewed baseline/target and emits only patch statistics/hashes, merge status, and candidate hashes; no candidate source content or production write is produced;
- current live reconciliation preserves `research_store.py` cleanly as candidate blob `31bd88e3d74490f5d0b617ff4b36383e7e12e18f`; the only remaining content-level blocker is `state_reader.rs`, where exactly two overlap regions are collected by `deploy/tools/collect_manual_market_paper_state_reader_conflict_hunks.py` under exact production-head/current-blob/conflict-count locks;
- `deploy/tools/build_manual_market_paper_research_store_candidate.py` re-runs that clean merge under exact production-head/base/current/target locks, requires candidate blob `31bd88e3d74490f5d0b617ff4b36383e7e12e18f`, and writes only a sealed candidate under `/var/tmp`; it never overwrites the production-local 51-line addition;
- the two exact state-reader overlaps are reviewed to resolve to their reviewed-target sides while preserving all non-overlap production-local changes; `deploy/tools/build_manual_market_paper_state_reader_candidate.py` writes the sealed candidate only under `/var/tmp`, and `deploy/tools/validate_manual_market_paper_state_reader_candidate.py` validates it only in a temporary reviewed-source copy with single-job Rust tests;
- candidate validation now seals host-toolchain blockers: an absent Cargo executable yields `validation_blocker=CARGO_NOT_FOUND` instead of a traceback; the validator may use an already-installed Cargo from PATH/common locations or an explicit `--cargo-bin`, but never installs or modifies a toolchain;
- when the production host has no Cargo, `deploy/tools/export_manual_market_paper_state_reader_candidate_patch.py` exports only the exact sealed candidate delta against the reviewed target under `/var/tmp`, bounded to 16 KiB / 20 hunks / 256 changed lines; this enables off-host validation without rebuilding the candidate, changing production, installing Rust, or publishing the full production-local file;
- `deploy/tools/validate_manual_market_paper_state_reader_portable_patch.py` validates that privately transferred patch on a non-production host: it reconstructs the exact candidate only in a temporary reviewed-source copy, requires candidate blob `f54a1021cf8f89d285bde957d1f72d81857ec2fa`, and runs default + `live-submit` Rust tests with an already-installed Cargo toolchain; the patch must not be published merely to obtain CI;
- `deploy/tools/export_manual_market_paper_research_store_candidate_patch.py` and `deploy/tools/validate_manual_market_paper_research_store_portable_patch.py` provide the matching bounded/off-host path for preserved research-store candidate `31bd88e3d74490f5d0b617ff4b36383e7e12e18f`, with temporary-source precedence and focused semantic Python tests;
- after both portable validations are ready, `deploy/tools/build_manual_market_paper_preservation_review.py` binds both candidate blobs to the known production-local blobs and sealed reconciliation evidence, emits only the exact two reviewed-source/manifest substitutions, and requires a reviewed-source rebase plus a completely fresh readiness/handoff/plan/gate cycle; it does not embed candidate contents or authorize mutation;
- the preservation-aware continuation uses a sealed private `/var/tmp` source bundle and the dedicated `check_manual_market_paper_preserved_readiness.py` -> `manual_market_paper_preserved_handoff.py` -> `build_manual_market_paper_preserved_deployment_plan.py` -> `check_manual_market_paper_preserved_deployment_gate.py` -> `build_manual_market_paper_preserved_mutation_review.py` chain, so the two preserved targets are never spliced into stale pre-preservation artifacts;
- `build_manual_market_paper_preserved_evidence_package.py` seals that complete fresh read-only lineage into one digest-only evidence package and keeps every mutation/service/cursor/timer/capital authorization false;
- `capture_manual_market_paper_preserved_backups.py` is the next safe stage after a complete evidence package: it re-checks expected-current production files, copies only backup-required rollback bytes into a new private `/var/tmp` tree with no-follow checks, verifies every rollback blob, and still requires a fresh execution-time recheck plus separate mutation authorization;
- `check_manual_market_paper_preserved_authorization_review.py` is the terminal machine-generated review stage: it re-runs the live preserved mutation review and requires exact equality with the saved review, re-reads every rollback backup with no-follow checks, seals the exact operation/target/rollback scope, and can report only `authorization_review_ready`; `authorization_granted` and every deployment/mutation/service/cursor/timer/capital authorization remain false;
- `build_manual_market_paper_preserved_human_authorization_request.py` is the only implemented continuation after that terminal review: it validates the exact sealed review through a pinned reviewed validator, binds the request to `authorization_review_sha256`, copies only the reviewed operation/target/rollback/backup identities, and still requires `approval_artifact_present=false`, `authorization_granted=false`, a fresh execution-time recheck, and separate mutation authorization;
- `build_manual_market_paper_preserved_signed_human_authorization.py` authenticates that exact request with a short-lived detached SSH signature under a dedicated namespace and an externally pinned `allowed_signers` trust-root digest; its `emit-signing-bytes` path removes JSON-formatting ambiguity, and even a successfully verified signature still reports `execution_ready=false`, `mutation_authorized=false`, and requires the fresh execution-time recheck;
- `check_manual_market_paper_preserved_execution_precheck.py` is the final read-only gate before any separately reviewed writer: it rebuilds the live terminal authorization review, re-verifies the short-lived signed human authorization against the pinned trust root and current expiry, requires exact saved/fresh equality, rebinds every operation/rollback/backup identity, and can report `execution_precheck_ready=true` only while still forcing `execution_ready=false`, `mutation_authorized=false`, and `requires_immediate_per_operation_recheck=true`;
- `apply_manual_market_paper_preserved_file_mutations.py` is the separately reviewed exact-scope file writer after that precheck: it locks the writer path, re-runs and exactly matches the saved precheck, preloads reviewed target/rollback bytes, rechecks each target immediately before its own install, verifies every result, and reverses earlier writes if a later operation fails; it emits a non-activation mutation receipt and has no Git/service/cursor/timer/transaction/live-capital action;
- `check_manual_market_paper_preserved_post_mutation.py` is the required read-only checkpoint after that writer: it binds the mutation receipt back to the execution precheck, re-reads every deployed target and rollback backup, rebuilds preserved readiness, and requires production HEAD plus observed detector/watcher/PAPER service/timer/pool/cursor state to match the pre-mutation handoff while the runtime is now fully deployed and manual PAPER remains inactive;
- `check_manual_market_paper_manual_cycle_readiness.py` then re-runs that complete post-mutation audit before any proof cycle, requires exact equality with the saved audit plus the reviewed MANUAL_ONLY runtime/CLI lineage, and still keeps manual-cycle execution/timer/signing/submission/live-capital authorization false;
- `build_manual_market_paper_manual_cycle_authorization_request.py` seals exactly one bounded proof-cycle request (account, unique run ID, virtual capital, modeled network cost, one new position maximum, at most 10 pools, scheduler position cap 1, and all other CLI bounds) without granting authorization; `build_manual_market_paper_manual_cycle_signed_authorization.py` authenticates only that exact request under its own detached-SSH namespace and still requires a fresh execution gate;
- `check_manual_market_paper_manual_cycle_execution_gate.py` is the last read-only boundary before the proof run: it rebuilds current readiness, re-verifies the still-valid signed one-cycle authorization, and requires exact saved/fresh equality; `run_manual_market_paper_one_cycle.py` is the separately reviewed one-shot PAPER executor that immediately rebuilds that gate again, runs only the exact bounded manual module against `/opt/pio/data/pio.db`, performs no retry/systemd/Git/live-submit action, and emits a sealed receipt requiring post-cycle audit;
- `check_manual_market_paper_one_cycle_post_cycle.py` is the read-only proof-cycle checkpoint: it requires the production activation-state audit to remain exactly unchanged, verifies the PAPER account with the reviewed runtime lineage, requires the PAPER event ledger to reconcile cleanly, and still leaves recurring timer/service/cursor/signing/submission/live-capital authorization false;
- `check_manual_market_paper_phase5_evidence_status.py` is the next read-only evidence stage: it binds that passing one-cycle audit, evaluates the exact existing Phase 5 72-hour/500-tick promotion criteria on a private SQLite online-backup snapshot, seals the endurance/ledger/count evidence plus current blocking reasons, and never persists promotion or authorizes recurring PAPER automation;
- `build_manual_market_paper_phase5_evidence_collection_plan.py` turns that status into exact remaining runtime/tick/valuation/position/pool deficits and separates evidence the five-minute scheduler can collect from new-entry/pool-diversity evidence that still requires separately signed one-cycle PAPER runs;
- `build_manual_market_paper_phase5_evidence_collection_request.py` -> `build_manual_market_paper_phase5_evidence_collection_signed_authorization.py` -> `check_manual_market_paper_phase5_evidence_collection_readiness.py` binds a finite one-to-72-hour collection window, authenticates it under a dedicated SSH namespace, rechecks current Phase 5 state plus exact installed systemd/env scope, and still keeps timer start/enable and every live-capital authorization false;
- `start_manual_market_paper_phase5_evidence_collection.py` is the bounded runtime starter: after one final exact readiness match it schedules a transient stop guard first, starts the already-disabled account PAPER timer without enabling it, and guarantees the guard targets both the timer and oneshot service at the deadline; `check_manual_market_paper_phase5_post_collection.py` then requires both units inactive, the timer still disabled, reviewed unit bytes/no-drop-ins intact, and rebuilds fresh Phase 5 evidence before reporting whether another plan is needed or promotion evidence is ready;
- the separate Phase 5 promotion chain is `build_manual_market_paper_phase5_promotion_request.py` -> `build_manual_market_paper_phase5_promotion_signed_authorization.py` -> `check_manual_market_paper_phase5_promotion_readiness.py` -> `persist_manual_market_paper_phase5_promotion.py` -> `check_manual_market_paper_phase5_post_promotion.py`: it binds only a fully ready Phase 5 evidence artifact, authenticates short-lived human intent, rechecks fresh evidence plus zero-row anti-replay state, writes exactly one immutable history row and one current `PHASE5_PROMOTION_V1` row under an exclusive/SQLite write lock, then read-only audits the persisted rows and fresh evidence; every controlled-live/signing/submission/live-capital flag remains false and Phase 6 is separate;
- after a confirmed Phase 5 post-promotion audit, the reviewed Phase 6 pre-live chain is `check_phase6_prelive_evidence_status.py` -> `build_phase6_prelive_promotion_request.py` -> `build_phase6_prelive_promotion_signed_authorization.py` -> `check_phase6_prelive_promotion_readiness.py` -> `persist_phase6_prelive_promotion.py` -> `check_phase6_prelive_post_promotion.py`: the status evaluates the unchanged pre-live defaults (10 valid simulated LIVE ENTER intents, 2 pools, 2 blocked paths, one executor wallet, zero post-simulation intents) from private Pio/execution SQLite snapshots; the signed request still requires a fresh anti-replay recheck; the writer changes only the exact two Phase 6 promotion-evidence rows; and the terminal audit requires `requires_separate_phase7_workflow=true` while controlled LIVE, live-submit, transaction signing/submission, and live capital remain false;
- capture a sealed production-state handoff with `deploy/tools/manual_market_paper_handoff.py`;
- turn that handoff into a deterministic, non-mutating operation list with `deploy/tools/build_manual_market_paper_deployment_plan.py`;
- immediately before any future separately reviewed mutation, run `deploy/tools/check_manual_market_paper_deployment_gate.py` so current production must still match the sealed handoff and saved plan exactly; preserve the emitted `gate_sha256` report as sealed review evidence;
- then run `deploy/tools/build_manual_market_paper_mutation_review.py` so the fresh gate, exact expected-current production blobs/absence, reviewed source targets, patch bytes, symlink safety, and rollback requirements are sealed as `review_sha256`;
- no conflict-evidence report, manifest, handoff, plan, passing gate, or passing mutation review authorizes deployment, service restart, timer enablement, detector cursor movement, transaction signing/submission, or real capital.

The production decision tree is therefore: fresh read-only readiness; if blocked, collect sealed conflict evidence, run read-only reconciliation, construct and privately validate the exact preserved candidates off production, then combine those validations into a sealed preservation review. After the private preserved-source bundle exists, restart through preservation-aware readiness -> sealed preserved handoff -> deterministic preserved plan -> fresh preserved gate -> preserved mutation review -> sealed evidence package -> verified private rollback-backup capture -> final read-only authorization review -> non-authorizing human-authorization request -> detached-signature human-authorization verification -> final read-only execution precheck -> exact-scope preserved file mutation writer -> read-only post-mutation audit -> fresh manual-cycle readiness -> bounded one-cycle authorization request -> detached-signature one-cycle authorization -> final one-cycle execution gate -> one-shot PAPER executor -> read-only post-cycle audit -> read-only Phase 5 evidence status -> evidence-collection plan -> bounded collection request -> detached-signature collection authorization -> fresh collection readiness -> bounded timer start with transient hard stop -> read-only post-collection audit. The file writer, one-shot PAPER executor, and bounded collection starter are the only reviewed mutation layers in this chain: the writer changes only sealed source-file scope; the one-shot executor writes only virtual PAPER database state under exact signed proof-cycle bounds; and the collection starter changes only runtime systemd state for a finite PAPER evidence window while keeping the timer disabled persistently and stopping both timer and oneshot service at the deadline. A ready post-window result may continue only through the separately signed/fresh-rechecked Phase 5 promotion chain; a confirmed Phase 5 promotion may then continue only through the separately signed/fresh-rechecked Phase 6 pre-live promotion chain. The Phase 5 and Phase 6 promotion writers each mutate only their exact promotion-evidence history/current row pair. The terminal Phase 6 audit still stops before Phase 7 controlled LIVE, transaction signing/submission, or live capital.

Current research paths:
- `DISCRETE_COMPLETED_BIN_V1`: OHLC inventory/IL studies.
- `SMALL_LP_CHAIN_PATH_V2`: standard-SPL fixed-share counterfactual replay over real chain snapshots.
- `OBSERVATION_BOUNDARY_REBALANCE_V1`: out-of-range withdraw/re-enter lifecycle replay.

Phase 2 now has source-backed implementations for liquidity shares, fees, rewards, composition fees, rebalance lifecycle, Solana receipt costs, add execution bounds and rebalance execution guards. Promotion is evidence-driven: code existence alone does not make a capability ready.

## Main research commands

```bash
cd python-learner
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pio collect-once
pio data-status
pio replay-chain --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --min-bin <MIN> --max-bin <MAX> --strategy SPOT --observations 12

pio replay-rebalance --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --half-width 5 --strategy CURVE --observations 24

pio scan-chain --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y>
pio reconcile-position --position <POSITION>
pio reconcile-corpus
pio phase2-evidence
pio phase2-work-queue

pio screen-pools
pio baseline-walk-forward --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --network-cost-y-atomic <COST>
pio size-position --equity 10000 --cash 10000 --deployed 0 --drawdown-bps 0
pio manage-position --active-bin 100 --min-bin 95 --max-bin 105 \
  --holding-observations 12 --rebalances-done 0
pio phase3-plan --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --requested-quote <NOTIONAL> --equity <EQUITY> --cash <CASH> \
  --deployed <DEPLOYED> --drawdown-bps <BPS> \
  --network-cost-y-atomic <COST>

pio paper-create-account --account paper --cash 10000
pio paper-open-phase3 --account paper --position <ID> --event-key <KEY> \
  --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --requested-quote <QUOTE> --network-cost-y-atomic <COST>
pio paper-status --account paper
pio paper-open --event-key <KEY> --account paper --position <ID> --pool <POOL> \
  --policy-source DETERMINISTIC --strategy SPOT --min-bin <MIN> --max-bin <MAX> \
  --capital <QUOTE>
pio paper-observe --event-key-prefix <KEY> --position <ID> --active-bin <BIN> \
  --holding-observations <N> --mark <QUOTE> --estimated-exit-cost <QUOTE> \
  --rebalance-cost <QUOTE>
pio paper-performance --account paper --policy-source DETERMINISTIC

# Chain-valued paper path
pio paper-chain-bind --position <ID> --observed-at <TIME> \
  --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --token-y-quote-per-atomic <QUOTE_RATE>
pio paper-chain-value --position <ID> --observed-at <TIME> \
  --token-y-quote-per-atomic <QUOTE_RATE>
pio paper-chain-observe --position <ID> --observed-at <TIME> \
  --token-y-quote-per-atomic <QUOTE_RATE> --rebalance-cost <QUOTE>
pio paper-chain-run --run-id <RUN> --observed-at <TIME> --file <ITEMS_JSON>
pio paper-live-latest-run --cycle-id <CYCLE> --file <ITEMS_JSON>
pio paper-portfolio-run --account paper --cycle-id <CYCLE> \
  --quotes-file <TOKEN_Y_QUOTES_JSON> --max-positions <N>
pio paper-tick --account paper --tick-id <ID> --refresh-jupiter-quotes
pio paper-scheduler-run --account paper --interval-seconds 300 --lease-seconds 900 \
  --refresh-jupiter-quotes
pio paper-scheduler-status --account paper
pio paper-health --account paper --require-healthy
pio paper-endurance-report --account paper --require-passing
pio paper-audit --account paper --require-passing
pio phase5-validate --account paper --require-ready
pio phase6-validate --execution-db /absolute/path/to/execution.db --require-ready

# Persisted promotion / ML workflow
pio phase-status  # includes Phase 9 future-policy simulation currentness
pio phase3-validate --file <POOLS_JSON>
pio ml-train-csv --file <DATASET_CSV> --model-id <MODEL> \
  --dataset-version <VERSION> --artifact-dir <DIR>
pio ml-offline-evaluate-csv --file <DATASET_CSV> --model-id <MODEL>
pio ml-start-paper --model-id <MODEL>
pio ml-model-status --model-id <MODEL>

pio phase8-evidence-status
pio phase8-evidence-plan
pio phase8-operator-handoff  # exact automatic/waiting/manual Phase 8 boundary
pio phase8-transition-history --require-ready  # append-only model/cycle transition journal audit
pio phase8-transition-snapshot --as-of <TIME> --require-consistent
pio phase8-historical-promotion --as-of <TIME> --require-ready  # reconstructed readiness only; does not persist promotion
pio phase8-historical-promotion-audit --as-of <TIME> --require-valid  # persisted promotion validity at cutoff
pio phase8-transition-snapshot --as-of <TIME> --require-consistent  # journal-backed model/cycle state only
pio phase8-evidence-step-run  # one safe offline planner-selected step
pio phase8-evidence-run --max-steps 4  # dataset build -> offline train -> offline validate; stops before PAPER/promotion
# Phase 8 consolidated status/plan/handoff are current-state only; historical --as-of fails closed
pio phase8-retrain-input-template > phase8-retrain-inputs.json
pio phase8-retrain-inputs-check --file phase8-retrain-inputs.json --require-valid
pio phase8-retrain-inputs-ingest --file phase8-retrain-inputs.json
pio phase8-retrain-inputs-audit --require-valid
pio phase8-retrain-build-run
pio phase8-retrain-train-run  # offline model creation only
pio phase8-retrain-offline-validate-run --require-qualified  # walk-forward + held-out offline gate

# Phase 9 research-only evidence
pio phase9-work-queue --rpc-url <RPC_URL>
pio phase9-storage-integrity --require-verified
pio phase9-replay-audit --require-verified
pio phase9-promotion-audit --require-current
pio phase9-operational-audit --require-verified
pio phase9-source-capture-run --history-min-observation-interval-seconds 3600 --api-ranking-max-age-seconds 10800  # bounded read-only pass
pio phase9-maintenance-status  # shared source/research lease state
pio phase9-maintenance-history --limit 20  # immutable unattended maintenance lifecycle
pio phase9-maintenance-health --require-healthy  # fail-able unattended maintenance health
# maintenance health honors persisted WAITING_INTERVAL next_retry_at before declaring a wait stale
pio phase9-source-freshness  # replay-valid evidence vs latest persisted source observations
pio phase9-evidence-status  # quantitative ranked-cohort/source/family readiness
# Historical Phase 9 --as-of views exclude future evidence, chain depth, assumptions, source freshness and Phase 8 promotion state
# Omitting --as-of uses true live/current audits; explicit --as-of plans/handoffs are inspection-only and emit no live collector/template commands
pio phase9-evidence-plan  # ranked evidence debt + one deterministic next safe action
pio phase9-evidence-step-run  # execute exactly one planner-selected safe evidence step
pio phase9-evidence-run --max-steps 8  # bounded safe steps until ready/manual/wait/failure/no-progress
# WAITING_SOURCE_ACTIVITY requires exhausted wallet backfill + zero deferred owner/position work + no collector failures
# WAITING_INTERVAL includes deterministic next_retry_at from persisted pool cadence
pio phase9-operator-handoff  # exact operator-owned blocker + non-inventing follow-up
pio phase9-research-refresh-run  # replay-aware automatic research refresh; no promotion
pio phase9-chain-capture-plan --rpc-url <RPC_URL> --require-ready
pio phase9-chain-capture-run --require-target  # uses SOLANA_RPC_URL; read-only inspect + local ingest
pio phase9-mint-capture-plan --target-pools 2 --require-ready
pio phase9-mint-capture-run --target-pools 2 --require-ready  # uses inspect-mint-env
pio phase9-position-discovery --pool <POOL> --limit 250
pio phase9-pool-activity-discovery --pool <POOL> --limit 25 --advance-backfill  # read-only historical signature page
pio phase9-wallet-flow-capture-run --pool <POOL> --require-ready
pio phase9-research-input-template --pools <POOL_A,POOL_B,POOL_C> > phase9-research-inputs.json
pio phase9-research-inputs-check --file phase9-research-inputs.json --require-valid
pio phase9-research-inputs-ingest --file phase9-research-inputs.json
pio phase9-research-inputs-audit --require-valid
pio phase9-explicit-research-run --persist --require-ready
pio phase9-bandit-research-run --persist --require-qualified  # derives checksum-bound labels from explicit inputs + chain replay
pio phase9-chain-history-plan --require-ready
pio phase9-chain-history-run  # one fresh read-only snapshot per deficient pool
pio phase9-chain-history-run --continue-sampling-when-ready  # manual ongoing cadence sample after minimum depth
pio phase9-work-queue --persist-snapshot  # research -> promotion -> policy-simulation blockers
pio phase9-progress --require-snapshot --require-integrity  # reports through PREWIRE_MANIFEST_CURRENT
pio phase9-shadow-validate --cycle-id <POST_PROMOTION_CYCLE_ID> --persist --require-ready
pio phase9-policy-authorization-gate --persist --require-ready
pio phase9-policy-authorization-audit --require-current
pio phase9-policy-controlled-validate --cycle-id <FRESH_HOLDOUT_CYCLE_ID> --persist --require-ready
pio phase9-policy-controlled-audit --require-current
pio phase9-policy-readiness-audit --require-ready
pio phase9-policy-rollout-simulate --file config/phase9_rollout_simulation.example.json --persist --require-ready
pio phase9-policy-rollout-audit --require-current
pio phase9-policy-rollback-simulate --file config/phase9_rollback_simulation.example.json --persist
pio phase9-policy-rollback-audit --require-current
pio phase9-policy-prewire-audit --require-ready
pio phase9-policy-manifest --persist --require-ready
pio phase9-policy-manifest-audit --require-current
pio phase9-research-validate --pools <POOL_A,POOL_B,POOL_C> --persist
pio mint-risk-research --pool <POOL> --persist --require-qualified
pio wallet-flow-research --pool <POOL> --persist --require-qualified
pio static-hedge-research --pool <POOL> --amount-x <ATOMIC_X> --amount-y <ATOMIC_Y> \
  --hedge-instrument-id <INSTRUMENT> --hedge-venue <VENUE> \
  --hedge-available-liquidity-y-atomic <LIQUIDITY> --persist
pio contextual-bandit-research --file <ML_ACTION_DATASET_CSV> --persist
pio phase9-research-bundle --persist --require-ready
pio phase9-research-bundle --as-of <TIME> --require-ready  # historical inspection only; --persist is rejected
pio phase9-validate --persist-ready --require-ready
```

Real execution/calibration commands:

```bash
pio collect-position-history --position <POSITION>
pio add-execution --position <POSITION>
pio rebalance-execution --position <POSITION>
pio transaction-costs --position <POSITION>
pio composition-prestate --position <POSITION>
pio reconcile-composition --position <POSITION>

# Reconciled controlled-live evidence (default build still cannot submit)
pio ingest-execution-receipt --file <RUST_RECEIPT_JSON>
pio apply-live-execution-effect --decision <DECISION_ID>
pio apply-live-position-effect --decision <DECISION_ID>
pio finalize-live-position-closure --decision <SETTLEMENT_DECISION_ID> \
  --file <CLOSE_PROOF_JSON>
pio build-live-position-outcome --position <POSITION>
pio value-live-position-outcome --position <POSITION> --max-age-seconds 300
pio ingest-execution-decision-context --file <DECISION_CONTEXT_JSON>
pio build-live-learning-label --position <POSITION>
pio live-execution-ledger-audit --require-clean
```

The work queue emits concrete read-only Rust commands when an existing sample can be completed. If historical prestate cannot be proven, it says so instead of reconstructing it approximately.

Phase 3 commands are research/policy tools. They do not build, sign or send live transactions. A pool must pass the fail-closed safety screen, a deterministic range/strategy must pass trailing replay, capital must fit sizing limits, and Phase 2 evidence must be promoted before the policy can authorize entry.

ML v1 is research-only: it learns from all replay-valid candidate actions at each decision point, uses time-ordered holdout validation, compares challengers against the deterministic baseline, and cannot become champion without qualified paper evidence. Paper commands mutate only the local paper ledger; they never sign or send a Solana transaction. The portfolio runner discovers eligible open chain-bound positions, requires explicit token-Y quote inputs, skips stale/unpriced positions, and uses idempotent latest-chain cycles with restart recovery. `paper-open-phase3` derives account risk state from the ledger and atomically opens plus chain-binds a persistently promoted deterministic plan.

## Rust execution preflight

Phase 6 can enforce risk, transaction/account/instruction policy, simulation,
isolated-wallet authorization, durable restart state, chain-resolved standard-SPL and
Token-2022 construction, deterministic signing, same-signature submission recovery,
confirmation and receipts. Python can persist a pre-live Phase 6 promotion only from a
multi-intent guarded presign corpus with zero pre-promotion signing/sending. Default
Rust builds omit live submission; the non-default `live-submit` build additionally
requires the runtime switch plus persisted Phase 5/6 and controlled-live gates.

See `docs/PHASE6_EXECUTION_RUNBOOK.md`.

## Rust read-only inspection

```bash
cd rust-executor

cargo run -- inspect-pool <RPC_URL> <POOL_ADDRESS> 1
cargo run -- inspect-position <RPC_URL> <POSITION_ADDRESS>
cargo run -- inspect-transaction-events <RPC_URL> <SIGNATURE>
cargo run -- verify-prestate <RPC_URL> <SIGNATURE> \
  <CAPTURE_START_SLOT> <CAPTURE_END_SLOT> <POOL_ACCOUNT> <BIN_ARRAY_ACCOUNT>
```

Rust JSON can be piped into:
- `pio ingest-chain-snapshot`
- `pio ingest-position-snapshot`
- `pio ingest-transaction-events`
- `pio ingest-prestate-verification`

These commands are read-only and require no wallet private key.

## Safety

20% daily return is an aspirational benchmark only. The bot cannot increase risk just to chase it. A no-trade day is valid.


## Unattended PAPER mode

Production-style PAPER scheduling templates live under `deploy/systemd/`. They
run as an unprivileged `pio` user, use deterministic leased scheduler ticks,
and can call only the Rust read-only pool inspector. Build the Rust executor
once with `cargo build --release` and set `PIO_RUST_EXECUTOR_BIN` plus
`SOLANA_RPC_URL`. No wallet private key is required or accepted by this path.
