# Bounded Phase 5 PAPER evidence collection loop

This runbook continues from a ready manual market/PAPER Phase 5 evidence-status
artifact and collects only the remaining PAPER evidence needed by the existing
Phase 5 promotion criteria.

Reviewed source:

```text
ca6a57559edbbf22919390d680a6dd079036927d
```

Reviewed tool blobs:

```text
check_manual_market_paper_phase5_evidence_status.py
f3b90f3100c23f8454172f3bb97482e31df5921d

build_manual_market_paper_phase5_evidence_collection_plan.py
bef3d59207630528becf362915e148f06b3cb03a

build_manual_market_paper_phase5_evidence_collection_request.py
a210f74f1228679b7db338e44d7789825a9ecc8d

build_manual_market_paper_phase5_evidence_collection_signed_authorization.py
d7e3cea9c402b06c8f3a1e7a2f2425fc75946760

check_manual_market_paper_phase5_evidence_collection_readiness.py
6c72b5d63cf92eb48048f16e68f9258fa3be13dc

start_manual_market_paper_phase5_evidence_collection.py
0b2f6eff265e61af2ef49daf58feab702232536d

check_manual_market_paper_phase5_post_collection.py
6c9f1f8fa3aa12ee4deed617637dcf6b0e569613
```

This is a **bounded PAPER evidence-collection path**. It is not Phase 5
promotion and it never authorizes live capital.

## What the loop does

The loop separates two evidence sources:

- the five-minute PAPER scheduler can accumulate runtime, terminal ticks,
  success/dependency-blocked rates, chain valuations, and management evidence
  for already-open PAPER positions;
- new-position and pool-diversity evidence cannot be created by this scheduler
  path and still requires separately signed bounded manual PAPER cycles.

Every scheduler collection window is finite. The reviewed starter:

- fresh-rechecks the signed collection readiness immediately before mutation;
- schedules a transient stop guard **before** starting the PAPER timer;
- starts the already-disabled account timer without enabling it;
- keeps the timer unit-file state disabled;
- makes the stop guard target both the account timer and account oneshot
  service at the deadline; and
- fails closed on reboot because the timer was never persistently enabled.

After the signed deadline, the post-collection audit independently requires the
timer and service to be inactive before it evaluates the resulting Phase 5
evidence.

## Prerequisites

Start from:

- a ready one-cycle post-cycle audit;
- a ready Phase 5 evidence status from
  `check_manual_market_paper_phase5_evidence_status.py`;
- persistent Phase 3 promotion;
- a passing PAPER ledger; and
- no historical failure-streak blocker that the next scheduler window cannot
  repair.

The collection request intentionally fails if Phase 5 is already promotion
ready or if recurring scheduler evidence is no longer needed.

## 1. Build the collection plan

Use one isolated checkout of the reviewed source:

```bash
set -euo pipefail

REVIEWED_REF="ca6a57559edbbf22919390d680a6dd079036927d"
SRC="$(mktemp -d /var/tmp/pio-phase5-collection-source.XXXXXX)"

POST_CYCLE_AUDIT="/var/tmp/pio-manual-paper-one-cycle-post-cycle-audit.json"
PHASE5_STATUS="/var/tmp/pio-manual-paper-phase5-evidence-status.json"

COLLECTION_PLAN="/var/tmp/pio-manual-paper-phase5-collection-plan.json"
COLLECTION_REQUEST="/var/tmp/pio-manual-paper-phase5-collection-request.json"
SIGNED_PAYLOAD="/var/tmp/pio-manual-paper-phase5-collection-signed-payload.json"
SIGNING_BYTES="/var/tmp/pio-manual-paper-phase5-collection-signing-bytes"
SIGNED_VERIFICATION="/var/tmp/pio-manual-paper-phase5-collection-signed-verification.json"
COLLECTION_READINESS="/var/tmp/pio-manual-paper-phase5-collection-readiness.json"
ACTIVATION_RECEIPT="/var/tmp/pio-manual-paper-phase5-collection-activation-receipt.json"
POST_COLLECTION_AUDIT="/var/tmp/pio-manual-paper-phase5-post-collection-audit.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/build_manual_market_paper_phase5_evidence_collection_plan.py" \
  --source-tree "$SRC" \
  --phase5-evidence-status "$PHASE5_STATUS" \
  > "$COLLECTION_PLAN"

python3 -m json.tool "$COLLECTION_PLAN"
```

Review:

- `current_evidence`;
- `evidence_deficits`;
- `health_checks`;
- `minimum_nominal_collection_hours`;
- `recurring_scheduler_evidence_needed`;
- `entry_diversity_evidence_needed`;
- `phase3_dependency_blocked`; and
- `historical_failure_streak_blocked`.

The plan is non-authorizing.

## 2. Build the bounded collection request

```bash
python3 "$SRC/deploy/tools/build_manual_market_paper_phase5_evidence_collection_request.py" \
  --source-tree "$SRC" \
  --collection-plan "$COLLECTION_PLAN" \
  > "$COLLECTION_REQUEST"

python3 -m json.tool "$COLLECTION_REQUEST"
```

The request binds:

- the exact account and plan digest;
- five-minute scheduler cadence;
- 900-second scheduler lease;
- `--max-positions 3 --refresh-jupiter-quotes`;
- a minimum one-hour collection window;
- a maximum 72-hour collection window; and
- a required automatic stop.

It still contains:

```text
collection_authorization_present=false
collection_execution_authorized=false
new_market_entry_authorized=false
paper_timer_enable_authorized=false
live_capital_authorized=false
```

## 3. Build and sign the human-authorization payload

The signing namespace is:

```text
pio-manual-market-paper-phase5-evidence-collection-authorization-v1
```

Build the short-lived payload:

```bash
APPROVER_PRINCIPAL="<APPROVER_PRINCIPAL>"
APPROVAL_KEY="<PRIVATE_SSH_SIGNING_KEY>"

python3 "$SRC/deploy/tools/build_manual_market_paper_phase5_evidence_collection_signed_authorization.py" \
  build-payload \
  --source-tree "$SRC" \
  --request "$COLLECTION_REQUEST" \
  --approver-principal "$APPROVER_PRINCIPAL" \
  > "$SIGNED_PAYLOAD"

python3 "$SRC/deploy/tools/build_manual_market_paper_phase5_evidence_collection_signed_authorization.py" \
  emit-signing-bytes \
  --source-tree "$SRC" \
  --request "$COLLECTION_REQUEST" \
  --payload "$SIGNED_PAYLOAD" \
  > "$SIGNING_BYTES"

ssh-keygen -Y sign \
  -f "$APPROVAL_KEY" \
  -n pio-manual-market-paper-phase5-evidence-collection-authorization-v1 \
  "$SIGNING_BYTES"

SIGNATURE="$SIGNING_BYTES.sig"
```

The approval lifetime is capped at 15 minutes.

## 4. Verify the signed authorization

The trust file and its SHA-256 must be provisioned independently.

```bash
ALLOWED_SIGNERS="/secure/trust/pio-preserved-allowed-signers"
EXPECTED_ALLOWED_SIGNERS_SHA256="<OUT_OF_BAND_PINNED_SHA256>"

python3 "$SRC/deploy/tools/build_manual_market_paper_phase5_evidence_collection_signed_authorization.py" \
  verify \
  --source-tree "$SRC" \
  --request "$COLLECTION_REQUEST" \
  --payload "$SIGNED_PAYLOAD" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$SIGNED_VERIFICATION"

python3 -m json.tool "$SIGNED_VERIFICATION"
```

A verified signature authenticates the exact collection request. It still does
not start or enable the timer.

## 5. Run the final collection readiness gate

```bash
python3 "$SRC/deploy/tools/check_manual_market_paper_phase5_evidence_collection_readiness.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --post-cycle-audit "$POST_CYCLE_AUDIT" \
  --collection-plan "$COLLECTION_PLAN" \
  --collection-request "$COLLECTION_REQUEST" \
  --signed-authorization-verification "$SIGNED_VERIFICATION" \
  --signed-payload "$SIGNED_PAYLOAD" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$COLLECTION_READINESS"

python3 -m json.tool "$COLLECTION_READINESS"
```

This gate re-evaluates current Phase 5 evidence, re-verifies the short-lived
signature, verifies the installed systemd units exactly, rejects drop-ins, and
parses the scheduler override from `/etc/pio/pio.env` without sourcing it.

Before activation it requires both account PAPER units inactive and the timer
disabled.

Extract and review the sealed readiness digest:

```bash
EXPECTED_COLLECTION_READINESS_SHA256="$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["collection_readiness_sha256"])' \
    "$COLLECTION_READINESS"
)"
printf '%s\n' "$EXPECTED_COLLECTION_READINESS_SHA256"
```

## 6. Start the bounded PAPER collection window

This is the runtime-mutation step in this loop.

```bash
python3 "$SRC/deploy/tools/start_manual_market_paper_phase5_evidence_collection.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --post-cycle-audit "$POST_CYCLE_AUDIT" \
  --collection-plan "$COLLECTION_PLAN" \
  --collection-request "$COLLECTION_REQUEST" \
  --signed-authorization-verification "$SIGNED_VERIFICATION" \
  --signed-payload "$SIGNED_PAYLOAD" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  --collection-readiness "$COLLECTION_READINESS" \
  --expected-collection-readiness-sha256 "$EXPECTED_COLLECTION_READINESS_SHA256" \
  > "$ACTIVATION_RECEIPT"

python3 -m json.tool "$ACTIVATION_RECEIPT"
```

The starter takes an exclusive activation lock and re-runs the entire readiness
gate before the first systemd mutation.

It schedules the transient stop guard first, then starts only:

```text
pio-paper@<account>.timer
```

It never calls `systemctl enable`.

At the signed deadline the guard stops both:

```text
pio-paper@<account>.timer
pio-paper@<account>.service
```

The receipt must continue to report:

```text
persistent_recurring_paper_automation_authorized=false
paper_timer_enable_authorized=false
new_market_entry_authorized=false
phase5_promotion_authorized=false
live_capital_authorized=false
```

## 7. After the deadline, audit the completed window

Do not run this audit before `collection_deadline`.

```bash
python3 "$SRC/deploy/tools/check_manual_market_paper_phase5_post_collection.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --post-cycle-audit "$POST_CYCLE_AUDIT" \
  --pre-collection-phase5-evidence-status "$PHASE5_STATUS" \
  --activation-receipt "$ACTIVATION_RECEIPT" \
  > "$POST_COLLECTION_AUDIT"

python3 -m json.tool "$POST_COLLECTION_AUDIT"
```

The audit independently requires:

- the signed deadline has passed;
- the PAPER service is inactive;
- the PAPER timer is inactive;
- the timer is still disabled;
- installed service/timer fragments still match reviewed bytes;
- no unit drop-ins exist; and
- fresh Phase 5 evidence can be rebuilt through the snapshot-only evaluator.

It records `evidence_advanced` without pretending that lack of progress is a
successful promotion.

## 8. Decide the next evidence step

If the audit reports:

```text
phase5_promotion_ready=false
requires_new_collection_plan=true
```

then the fresh embedded `fresh_phase5_evidence_status` is the basis for the
next reviewed collection decision.

If `entry_diversity_evidence_needed=true`, the scheduler cannot satisfy that
deficit by itself. Use the separately signed bounded one-cycle PAPER path for
new entry/pool evidence, then rebuild Phase 5 status and plan again.

If the audit eventually reports:

```text
phase5_promotion_ready=true
requires_new_collection_plan=false
```

stop the collection loop.

## Stop boundary

A ready post-collection audit still contains:

```text
phase5_promotion_persisted=false
phase5_promotion_authorized=false
persistent_recurring_paper_automation_authorized=false
paper_timer_enable_authorized=false
service_restart_authorized=false
detector_cursor_movement_authorized=false
transaction_signing_authorized=false
transaction_submission_authorized=false
live_capital_authorized=false
```

Phase 5 promotion persistence remains a separate future action that must consume
ready evidence explicitly. No Phase 6/live-capital gate may treat collection
time alone as equivalent to Phase 5 promotion.
