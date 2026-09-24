# Meteora Adaptive LP Bot — Build Phases

Status: v1.3

## Phase 0 — Foundation
Status: complete.

## Phase 1 — Market Data Engine
Status: implementation complete; extended live validation pending.

## Phase 2 — Position Simulator / Backtester
Status: standard-SPL implementation complete; promotion evidence pending.

Built:
- high-precision DLMM bin math
- SDK-equivalent Spot/Curve/Bid-Ask allocation
- verified empty/existing-bin liquidity-share formulas
- withdrawal/pro-rata amount math
- exact fee-checkpoint math
- effective reward-checkpoint math
- target-time dynamic fee-state replay
- active-bin composition-fee model
- slot-bounded pre-add pool captures
- exact pool/bin-array prestate gap verification
- exact positive CompositionFee event reconciliation
- read-only pool/bin/DynamicPosition Rust inspection
- multi-snapshot small-LP chain replay
- reward attribution
- observation-boundary rebalance lifecycle replay
- Token-2022 fail-closed gate
- exact amount/fee/reward DynamicPosition reconciliation
- full-history reconciliation corpus
- Anchor event-CPI decoding for AddLiquidity, CompositionFee, RemoveLiquidity and Rebalancing
- transaction receipt fee/compute-unit capture
- requested-vs-actual add execution calibration
- rebalance transaction guard decoding and calibration
- evidence-driven Phase 2 capability gate
- actionable Phase 2 calibration work queue
- chain-backed candidate scanner

Promotion still requires real evidence:
- a meaningful fresh real-position corpus with positive reward growth
- enough verified positive composition-fee samples
- complete add execution request/event coverage for the selected corpus
- enough real rebalance guard samples with no unexplained violations
- real transaction-fee receipt coverage
- extended high-frequency chain collection

Token-2022 transfer-fee support remains outside the current high-fidelity path and fails closed.

Exit condition:
- exact deterministic amount/fee/reward reconciliation passes;
- exact positive composition-fee samples pass;
- add/rebalance execution guards pass at the configured sample minimums;
- transaction-cost sample minimums pass;
- operator-selected coverage/sample thresholds pass;
- no required capability remains unvalidated.

## Phase 3 — Deterministic Baseline Strategy
Status: deterministic implementation complete; promotion evidence pending.

Built:
- fail-closed pool universe safety screen
- standard-SPL/token-program eligibility check
- TVL, volume, age, blacklist and dynamic-fee filters
- trailing chain candidate comparison by range/strategy
- decision-time proposal recentering
- cost-aware excess-versus-hold economics
- entry-value normalized return / hold / excess metrics
- equal-notional cross-pool research comparison
- non-overlapping no-lookahead walk-forward evaluation
- multi-pool walk-forward validation workflow
- drawdown-aware capital sizing
- cash reserve / per-position / total-deployment caps
- deterministic HOLD / REBALANCE / EXIT policy
- hard safety exit and stop-loss override
- optional take-profit and max-hold exit
- rebalance-count cap
- Phase 3 entry authorization gate
- end-to-end single-pool and multi-pool research planners
- evidence-driven Phase 3 promotion thresholds
- persistent Phase 2 / Phase 3 promotion evidence and status

Promotion still requires:
- persisted Phase 2 promotion from real reconciliation/calibration evidence
- a meaningful multi-pool, regime-diverse walk-forward corpus
- configured Phase 3 sample, hit-rate, excess-return and downside thresholds to pass
- persisted Phase 3 promotion evidence before downstream ML/paper gates can treat the policy as promoted

The baseline never ranks by headline APR alone. Research output can exist before promotion, but downstream promotion-sensitive workflows read persisted gate state rather than caller-supplied booleans. Live transaction construction/signing remains outside Phase 3.

## Phase 4 — ML v1
Status: experimental infrastructure implemented; promotion evidence pending.

Built:
- no-lookahead decision-time feature dataset
- all-candidate action labels for Spot / Curve / Bid-Ask alternatives
- forward net-return / excess-vs-hold / range-survival labels
- time-ordered train/validation split
- expected net-return model
- excess-vs-hold model
- downside model
- range-survival model
- positive-edge probability model
- research-only risk-adjusted inference
- held-out challenger versus deterministic-baseline evaluation
- persistent model registry
- reproducible model artifact save/load with metadata checks
- persisted offline challenger evidence
- staged OFFLINE_CANDIDATE -> OFFLINE_QUALIFIED -> PAPER_CHALLENGER lifecycle
- persistent Phase 3 promotion required before offline qualification
- evidence-gated CHAMPION promotion
- single-champion guard and rollback state
- CLI workflow for train/register, offline evaluation, paper start and model status

Still needed:
- materially larger multi-pool action dataset
- regime-diverse time windows
- stable real-data offline challenger qualification
- repeated paper validation before any challenger becomes champion
- retraining cadence and drift monitoring for Phase 8

## Phase 5 — Live Paper Trader
Status: implementation complete; persisted real PAPER promotion evidence pending.

Built:
- persistent paper accounts
- persistent paper positions and idempotent event ledger
- cash/equity/high-water/drawdown accounting
- fee/reward/cost/realized-PnL accounting
- deterministic paper HOLD / REBALANCE / EXIT evaluation
- automatic single-position paper observation cycle
- idempotent multi-position paper observation batches with restart recovery
- stop-loss and safety exits on net liquidation economics
- same-width deterministic recentering for paper rebalances
- persistent counterfactual per-bin liquidity-share/checkpoint state
- chain-derived inventory marks from real bin snapshots
- incremental chain-derived fee and supported reward valuation
- explicit current token-Y quote-rate support
- fail-closed handling for unvalued reward tokens / unsupported token programs
- prepared/applied chain valuation state for idempotent recovery
- counterfactual atomic-state reset after paper rebalances
- multi-position chain-valued paper runner
- latest-chain grouping across pools with idempotent cycle IDs
- fail-closed live pool safety derived from local normalized state
- restart recovery after prepared valuation and partially-applied mark events
- automatic account-level discovery of open chain-bound paper positions
- stale-first capped portfolio scheduling
- explicit token-Y quote-map requirement with missing-quote skips
- fresh persisted external reward-token quote valuation with no-lookahead lookup and fail-closed missing/stale handling
- ledger-derived Phase 3 paper entry workflow
- atomic paper position + ENTER event + counterfactual chain binding
- preflight counterfactual validation before capital debit
- focused Data API refresh for pools behind open positions
- read-only Rust refresh for missing/stale chain snapshots
- persisted token quote registry with freshness checks
- optional Jupiter USD-per-atomic token-Y quote refresh
- idempotent end-to-end paper ticks
- durable per-account scheduler lease and health state
- persisted scheduler lifecycle event history for lease acquisition, overlap, stale-worker recovery and tick completion
- deterministic time-bucket tick IDs for cron/systemd retries
- stale RUNNING tick recovery after expired scheduler leases
- prebuilt Rust executor support for hardened unattended refresh
- unprivileged systemd service/timer deployment templates
- current health report with Prometheus-compatible gauges
- evidence-based PAPER endurance report with configurable runtime, reliability, dependency-blockage and applied-valuation thresholds
- decimal-exact PAPER event-ledger reconciliation audit
- evidence-gated persistent Phase 5 promotion requiring Phase 3 promotion, endurance, multi-position/pool coverage, closed-position samples and clean accounting
- paper cohort performance metrics
- ML paper challenger versus deterministic baseline validation
- stored paper evidence required for champion promotion

Still needed:
- long endurance/restart runs across many observations and positions
- extended paper validation on real live observations
- external alert delivery/integration around the implemented health metrics, if required by deployment

## Phase 6 — Rust Transaction Executor
Status: implementation complete behind fail-closed gates; controlled live validation pending and public signing/sending disabled.

Built:
- isolated Rust wallet loader using an absolute owner-only keypair file
- symlink / broad-permission / inline-key fail-closed wallet handling
- independent Rust proposal risk gate
- serialized transaction decoder and RPC simulation
- proposal-to-transaction binding for pool and fee payer
- explicit program allowlists
- action-bound instruction discriminator / data-prefix allowlists
- instruction-count and static-account-count limits
- fail-closed address-lookup-table policy
- unsigned-transaction enforcement
- durable execution-intent journal keyed by immutable decision ID
- risk / transaction-policy / transaction-guard persistence
- monotonic idempotent execution state transitions
- simulation blocked until an accepted transaction guard is persisted
- executor-wallet authorization bound to the guarded fee payer
- fresh confirmed blockhash preparation with unsigned-message preservation
- exact prepared-transaction guard and wallet re-authorization before signing
- exact-blockhash final simulation with blockhash replacement disabled
- persisted final presign transaction/simulation evidence
- signing state blocked until wallet authorization is persisted
- restart-safe confirmation reconciliation for already-sent signatures
- read-only Solana confirmation lookup with transaction-history search
- terminal Rust execution receipt export linked to immutable decision ID
- immutable Python execution-receipt ingestion with optional Solana snapshot reconciliation
- unsigned standard-SPL Meteora RemoveAllLiquidity emergency-exit builder
- chain-resolved emergency-exit account derivation and ownership validation
- strict pinned RemoveAllLiquidity discriminator policy for EXIT actions
- unsigned standard-SPL live entry builder using deterministic executor-wallet position PDA
- chain-resolved entry pool/token/bin-array validation with missing-bin initialization
- unsigned standard-SPL rebalance builder consuming precomputed validated remove/add parameters
- chain-resolved rebalance position ownership, pool/token and bin-array validation
- isolated deterministic one-signer transaction signer bound to persisted final-presign evidence
- idempotent internal submission coordinator that persists SENT/signature before RPC submission and retries only the same signed transaction
- expiry-aware SENT recovery that blocks blind resubmission after last-valid block height
- chain-resolved standard-SPL fee/reward settlement and final ClosePosition2 builder
- confirmed RPC proof that final position account closure occurred after settlement
- receipt-driven immutable live atomic wallet-effect ledger
- receipt-driven live position lifecycle state for ENTER / REBALANCE / liquidity-removal EXIT / final CLOSE
- immutable CLOSED-position atomic outcome evidence including linked network fees
- no-lookahead quote-valued CLOSED live position PnL using execution-time persisted token/SOL quotes
- immutable Rust execution-decision context export and Python receipt-reconciled ingestion
- immutable learner labels joining confirmed ENTER model/strategy/range/capital to realized live return and prediction error
- fail-closed live execution ledger integrity audit including valuation/learning-label completeness
- Token-2022 transfer-hook / remaining-account entry, rebalance, exit and settlement construction
- Rust read-only Phase 5 promotion verification against persisted Python evidence
- Phase 6 deployment-readiness gate binding Phase 5 promotion, isolated wallet and strict action policy
- internal submission coordinator refuses live submission without accepted Phase 5 and Phase 6 promotion gates
- persistent Phase 6 pre-live promotion evidence from multi-pool guarded presign and blocked-path corpus
- Rust read-only verifier for persisted `PHASE6_PROMOTION_V1`
- Phase 6 execution runbook and reproducible guard/request examples

Still needed:
- real Phase 6 promotion corpus after Phase 5 promotion evidence exists

Default builds exclude the `live-submit` feature. A compile-time + runtime gated controlled-live submit path exists for later validation, but it is disabled by default and has not been approved for production live trading.

## Phase 7 — Controlled Live Trading
Status: controlled-live execution path implemented behind compile-time/runtime/promotion/risk gates; first controlled-live validation run pending.

Built:
- read-only Rust controlled-live gate against persisted Python live-position state
- mandatory Phase 5 promotion dependency
- explicit pool allowlist for ENTER / REBALANCE
- hard per-entry quote-capital cap
- max concurrent non-closed live-position cap
- daily drawdown gate for ENTER / REBALANCE
- disabled-by-default live kill switch
- EXIT remains independently allowed for risk reduction when entry/rebalance are disabled
- reproducible disabled example configuration
- internal submission coordinator requires controlled-live authorization bound to the same decision ID, action, pool and Phase 5 evidence
- compile-time `live-submit` feature is off in default builds
- runtime `PIO_LIVE_SUBMIT_ENABLED=1` switch is additionally required
- Phase 6 readiness must match the same isolated wallet and Phase 5 evidence
- unresolved ENTER intents consume controlled-live concurrency
- daily submitted ENTER capital budget
- daily ENTER submission-count cap
- independent daily realized-loss budget derived from confirmed valued live outcomes
- any same-day CLOSED outcome lacking valuation blocks new ENTER / REBALANCE
- exactly one tracked active position is required for pool-level REBALANCE / EXIT authorization
- hard per-position rebalance-count cap
- persistent Phase 7 controlled-live promotion evidence requiring clean fully reconciled closed-position corpus
- Rust read-only verifier for persisted `PHASE7_PROMOTION_V1`

Still needed:
- real Phase 6 promotion evidence
- first small-capital controlled-live runs with full receipt/ledger reconciliation
- real Phase 7 promotion evidence before widening limits or enabling unattended live execution

## Phase 8 — Continuous Learning

Status: implementation complete behind evidence gates; real continuous-learning promotion evidence pending.

Built:
- persisted champion/challenger model registry with immutable status transitions
- evidence-driven retraining trigger from fresh chain observations, pool coverage, live labels and champion age
- one-active-cycle continuous retraining state machine
- deterministic cutoff-bound multi-pool retraining datasets with dataset identity/checksum binding
- cycle-bound challenger training that rejects tampered or mismatched datasets
- no-lookahead walk-forward challenger validation with persisted cycle-bound evidence
- paper challenger validation against incumbent champion performance
- champion rotation requires qualified walk-forward evidence, matching dataset lineage and paper evidence
- immutable continuous-promotion evidence linking predecessor, challenger and cycle
- live champion monitoring from realized labels, including drawdown, worst loss, win rate, mean return and prediction error
- minimum live-label and distinct-pool diversity requirements for champion health
- persisted live-monitor evidence and evidence-gated rollback to deterministic policy
- persistent Phase 8 promotion evidence requiring Phase 7 promotion, completed continuous cycles, current-champion lineage and healthy live evidence
- CLI workflows for retraining planning/build/train/walk-forward, challenger validation/promotion, live monitoring/rollback and Phase 8 validation
- consolidated Phase 8 evidence status plus advisory ordered evidence planner spanning Phase 7 dependency, champion/cycle lineage, retraining triggers, live-label health and promotion currentness
- checksum-bound current-champion retraining input artifacts with non-inventing pool templates and a direct dataset/cycle builder; explicit token amounts and network costs remain operator-supplied while model training/promotion stay separate stages
- checksum-verified offline retraining workflow that resolves the cycle dataset evidence/file, trains a deterministic challenger artifact, requires cycle walk-forward plus held-out offline qualification, and stops at OFFLINE_QUALIFIED before PAPER/champion promotion
- direct one-step and bounded Phase 8 offline evidence runners that dispatch only dataset build, deterministic offline challenger training and offline validation, stopping on real-evidence waits or any operator/PAPER/promotion boundary without shell execution
- non-persisting Phase 8 retraining-input preflight that validates the current champion lineage and computes the exact normalized artifact checksum before append-only input evidence is written
- deterministic Phase 8 operator handoff that separates safe offline automation, passive real-evidence waits, explicit retraining economics, and operator-owned PAPER/promotion/safety actions without performing those manual transitions
- current-only consolidated Phase 8 status/planning/handoff semantics that reject historical cutoffs until immutable model/cycle transition history exists, avoiding mixed-time promotion answers
- database-enforced immutable live-learning labels plus a cutoff-safe historical Phase 8 promotion evaluator using transition-journal state, Phase 7 promotion history, continuous-promotion evidence and only labels persisted by the requested cutoff; historical evaluation remains read-only and never persists Phase 8 promotion
- historical persisted-promotion audit that selects the latest Phase 8 promotion-history row at the cutoff, replays its persisted criteria against the cutoff-safe evaluator, and verifies champion/cycle/continuous-promotion lineage without consulting future rows
- append-only Phase 8 model/cycle transition journals with immutable SQLite triggers, migration-start watermark, legacy-row baseline backfill and fail-able coverage/trigger audit; historical consolidated evaluation remains disabled until it is explicitly rebuilt on this journal
- journal-backed Phase 8 historical state snapshot for cutoffs at/after the migration watermark, reconstructing Phase 7 promotion visibility plus per-model and per-cycle state while detecting impossible multiple-champion/active-cycle states; this is state reconstruction only, not historical promotion readiness

Still needed:
- real Phase 7 promotion evidence
- at least one real completed continuous retraining cycle over fresh post-champion data
- live champion evidence meeting Phase 8 label and pool-diversity thresholds
- real Phase 8 promotion evidence before any Phase 9 adaptive edge is allowed to affect live policy

## Phase 9 — Advanced Edge

Status: research implementation complete behind a non-actionable evidence boundary; real qualified research-bundle evidence pending.

Built:
- no-lookahead adaptive range research from persisted DLMM active-bin movement
- no-lookahead DLMM regime classification integrated into multi-pool adaptive validation
- empirical holding-window displacement coverage with explicit history/coverage/cap evidence
- Phase 8 promotion visibility in advanced-edge reports
- hard `research_only=true` / `policy_actionable=false` boundary for adaptive-range output
- authoritative read-only Solana mint inspection with immutable Python snapshot ingestion
- no-lookahead pool mint-risk research covering token-program consistency, initialization, mint/freeze authorities, decimals, reward mints and conservative Token-2022 extension handling
- descriptive wallet-flow research with minimum-history/user-diversity and concentration filters
- capped research-only multi-pool portfolio allocation with per-pool concentration and budget-utilization evidence
- static inventory hedge research with explicit instrument identity, venue, liquidity-share, leverage, funding and trading-cost assumptions
- offline contextual-bandit replay over fully labeled counterfactual actions, with adversarial no-lookahead tests and exploratory fixed-CSV replay
- checksum-bound cycle contextual-bandit qualification tied to persisted retraining dataset version, SHA-256, cutoff and evidence ID
- checksum-bound Phase 9 contextual-bandit dataset artifacts derived directly from validated explicit pool/economic inputs plus persisted no-lookahead chain replay when no continuous-retraining dataset exists, with common multi-pool cutoff, deterministic rebuild verification and a bounded latest-96-observation source window
- persistent Phase 9 research-bundle readiness gate spanning adaptive/regime, mint risk, wallet flow, allocation, hedge and bandit evidence while remaining `policy_actionable=false`
- persistent non-actionable Phase 9 promotion evidence requiring Phase 8 promotion and a current immutable ready research bundle; this milestone remains `research_only=true`
- dependency-aware Phase 9 evidence work queue that stages authoritative mint snapshots before mint-risk research and surfaces artifact/assumption gaps without inventing inputs
- immutable source-lineage verification across adaptive/regime chain snapshots, wallet-flow event windows, static-hedge pool/bin price paths, mint snapshots, portfolio candidate artifacts and contextual-bandit retraining datasets
- deterministic source hashes for wallet-flow, adaptive/regime, static-hedge and portfolio-candidate evidence with forged/stale lineage tests and repair tasks
- deterministic full-report replay across all qualified Phase 9 research families so source-valid but metric-forged evidence fails closed
- explicit read-only replay audit across every required Phase 9 research family, including checked evidence IDs and fail-able verification status
- database-enforced append-only Phase 9 source/evidence history plus immutable promotion-history records
- fail-able Phase 9 storage-integrity preflight required by bundle readiness and replay audit
- absolute regular-file / non-symlink retraining dataset requirements with replay-time byte checksum verification
- fail-closed range-cap and insufficient-history states
- CLI research workflow with optional readiness exit status
- Phase 8 promotion currentness audit required by every Phase 9 research family, bundle and work queue, so rollback/lineage/health degradation fail closed
- consolidated Phase 9 operational audit spanning immutable storage, deterministic replay and promotion currentness
- sanitized append-only Phase 9 work-queue progress snapshots with checksum-verified blocker delta reporting
- optional hourly systemd progress snapshot timer that never executes research commands or changes policy
- post-promotion checksum-bound shadow validation corpus requiring current Phase 9 promotion and newer independent retraining data while remaining non-actionable
- replay-verified future LIVE-policy authorization evidence gate requiring multiple unique shadow cycles/datasets/cutoffs and aggregate validation depth while remaining disconnected from execution
- fresh post-authorization simulation-only holdout validation requiring current authorization evidence, a new cycle and dataset hash, a cutoff after authorization creation, deterministic replay and persisted currentness audit
- consolidated fail-able Phase 9 policy-readiness audit requiring both current authorization evidence and current fresh-holdout controlled validation while remaining non-actionable
- disabled bounded-rollout simulation that requires a proposed Phase 9 canary envelope to stay within the existing controlled-live allowlist/caps, remain strictly narrower by default, preserve EXIT, disable REBALANCE and persist only non-actionable simulation evidence
- explicit rollback-trigger simulation with user-supplied sample-depth, loss/drawdown, performance and data-integrity thresholds; stale rollout evidence or hard breaches fail toward rollback while insufficient clean samples remain observation-pending
- consolidated non-actionable Phase 9 pre-wiring audit requiring current policy-readiness, current bounded-rollout simulation and a current rollback simulation resolved to `NO_ROLLBACK_TRIGGER`
- dependency-aware Phase 9 work queue and checksum-verified progress snapshots now continue past research promotion through shadow evidence, authorization, controlled holdout, bounded rollout, rollback simulation and pre-wiring readiness
- immutable checksum-bound pre-wiring manifest binding the exact authorization, controlled-validation, rollout and rollback evidence IDs/hashes, with automatic staleness when any component advances
- deterministic read-only chain-capture planner that converts API-discovered Meteora pools missing chain evidence into ranked `inspect-pool -> ingest-chain-snapshot` commands, with work-queue routing to discovery when no candidates exist
- explicit manual Phase 9 chain-capture batch that reuses the hardened read-only Rust inspector, isolates per-pool failures, validates returned pool identity and only ingests local snapshots; it never signs or submits transactions
- exact adaptive/regime history-depth planner derived from evaluator criteria (43 observations per pool under defaults) plus a one-fresh-snapshot-per-deficient-pool read-only capture runner with monotonic timestamp checks
- freshness-aware authoritative mint capture planner/runner using environment-based Rust `inspect-mint-env`, exact-pool targeting, deduplicated mint requirements, stale-snapshot refresh and fail-closed historical cutoffs
- RPC-filtered read-only `PositionV2` discovery by pool plus bounded current-owner expansion through Meteora `status=all` position PnL, round-robin owner sampling and official position-history collection for wallet-flow source coverage, while explicitly refusing to treat the current-owner cohort as a complete historical census
- bounded read-only historical pool-activity discovery using Solana `getSignaturesForAddress`, exact-pool Meteora event decoding, persistent backward pagination and recent-page rescans to surface closed/no-longer-current position candidates for wallet-flow history collection without claiming a complete historical census
- checksum-bound explicit research-input artifacts for hedge and portfolio assumptions, including a non-inventing template generator, strict validation, append-only persistence and a direct runner that binds candidate artifacts back to the exact input evidence ID/SHA
- bounded manual Phase 9 source-capture pass that can refresh public API discovery, fill missing chain-pool coverage, take one fresh history sample per deficient pool, refresh mint state and collect bounded wallet-flow sources while remaining read-only and non-actionable
- post-write live evaluation watermarks and post-history source retargeting in the Phase 9 source-capture pass: API ranking is frozen only after API refresh, post-chain/post-history/final cohort readiness advance to later cutoffs, history-induced fallback changes retarget mint/wallet acquisition before those collectors run, and final wallet readiness is checked against the final cohort
- explicit chain-history sampling cadence guard plus optional hardened systemd source-capture timer (70-minute activation, 3,600-second per-pool minimum) so retries cannot inflate observation depth with near-duplicate snapshots
- post-threshold continuous chain-history sampling for the established Phase 9 pool cohort: unattended source capture keeps one cadence-guarded fresh observation per selected pool after minimum history depth, so adaptive/regime/portfolio/bandit research can continue learning from new market regimes instead of freezing at first qualification
- replay-aware deterministic research-refresh pass for adaptive/regime, mint-risk, wallet-flow and checksum-bound contextual-bandit evidence, with storage/Phase-8 preflight, source gating, append-only dedup and ready-bundle persistence but no promotion
- timestamp-aware source-freshness tracking across adaptive/regime, mint-risk, wallet-flow, portfolio allocation, static hedge and contextual bandit evidence; scheduled refresh recomputes replay-valid families when genuinely newer source observations arrive while ignoring later-inserted older historical backfill
- explicit-assumption semantic freshness: identical normalized input artifacts are deduplicated, changed assumption SHA invalidates static-hedge/portfolio/Phase-9-derived-bandit currentness, and an invalid latest assumption artifact fails explicit-backed families closed until repaired
- deterministic historical `as_of` source freshness for adaptive/mint/wallet evidence: later live rows are ignored, while evidence whose own source lineage crosses the requested cutoff is rejected
- cutoff-safe Phase 9 research-bundle evaluation that uses historical Phase 8 persisted-promotion validity, selects only Phase 9 evidence created/dated by the cutoff, propagates the same cutoff through lineage replay, and rejects persistence of reconstructed historical bundles
- historical Phase 9 status now time-bounds cohort depth/source fallback, explicit-input artifact selection, source-freshness evidence rows, explicit-backed portfolio/static/bandit lineage, pool watermarks and retraining-dataset comparison so later observations or assumptions cannot leak backward
- policy-authorization source-currentness gate layered on top of immutable Phase 9 promotion history, so newer research sources revoke downstream controlled-validation/prewire readiness without mutating historical promotion evidence
- optional hardened systemd research-refresh timer offset behind source capture, with static tests preventing promotion, RPC capture, signing or LIVE-submit behavior from entering the scheduled research writer
- shared SQLite Phase 9 maintenance lease serializing source capture and research refresh, with stale-lease recovery and a 30-minute lease around 20-minute bounded services
- artifact-backed automatic refresh for static hedge and portfolio allocation when (and only when) a valid checksum-bound explicit-input artifact already exists; identical recomputations reuse evidence IDs instead of appending duplicates
- freshness-bounded API pool ranking for Phase 9 cohort steering and chain onboarding, with a 3-hour live default, deterministic historical `as_of` cutoffs, future-row exclusion before per-pool ranking and chain-depth fallback when discovery is stale
- shared ranked-cohort source selection across adaptive/regime, authoritative mint and wallet-flow acquisition/research, with operational freshness invalidating old qualified mint/wallet evidence when the ranked target set moves and chain-depth fallback only when ranked observed coverage is insufficient
- Phase 9 progress now exposes ranked cohort freshness plus per-pool chain observation depth and maximum fresh history samples remaining toward the exact qualification floor
- deterministic Phase 9 evidence-debt planner that ranks Phase 8 dependency, fresh API coverage, chain onboarding/history depth, mint freshness, wallet-flow source deficits, explicit inputs and research refresh, emitting one executable next safe action while remaining research-only
- lease-protected one-debt evidence runner that directly dispatches exactly one planner-selected API/chain/history/mint/wallet/research-refresh operation without shell execution, while returning manual blockers unchanged and remaining disconnected from promotion or LIVE policy
- lease-protected bounded evidence runner that chains only successful debt-reducing automatic steps and stops on READY, manual assumptions, cadence wait, collector failure, no-progress or a hard max-step bound
- optional hardened systemd evidence-runner timer that advances only bounded planner-selected source/research debt under the shared maintenance lease and cannot promote, authorize policy, sign or submit
- immutable Phase 9 maintenance lifecycle journal covering acquired/busy/recovered leases and finished outcomes for unattended source, refresh and evidence-run operations, with bounded CLI history inspection
- fail-able Phase 9 maintenance health report plus network-isolated systemd health timer covering stale history, failed/partial/no-progress runs, manual blockers, active runs and expired orphaned leases
- deterministic Phase 9 operator handoff report that converts manual debt into an exact non-inventing template/check/ingest/audit/research sequence and separates operator actions from economic-input requirements
- non-persisting explicit-input validation/checksum command so malformed or inconsistent assumptions can be rejected before append-only evidence ingestion

Still needed:
- real multi-pool evidence satisfying the persisted Phase 9 research-bundle thresholds
- explicit future LIVE-policy wiring remains intentionally unimplemented; rollout and rollback design are simulation-only and require real evidence before any separate authorization design
