# One bounded manual market/PAPER proof cycle

This runbook covers exactly one human-authorized manual market/PAPER proof
cycle after the preserved production file mutation has passed its post-mutation
audit.

It does **not** authorize recurring PAPER automation, a service restart,
detector cursor movement, transaction signing/submission, or live capital.

## Reviewed implementation lineage

| Stage | Reviewed commit | Tool blob |
| --- | --- | --- |
| Manual-cycle readiness | `c59e7043d8731040ec5c6263bde807a803df6c33` | `1e75644f9b4ea33738e8c4e7fddbdc4d08c98826` |
| One-cycle authorization request | `5fd3f30c35fd95f1e3fc50ab09ba003b330bff4f` | `9a967b85585f46cf552e43d71afa948153b59546` |
| Signed one-cycle authorization | `e60bec80333e6e2769ab4f549c58f803d063cf17` | `9ff655267a8ac51573858d7cd37e451bb25dc7da` |
| Final one-cycle execution gate | `de2eee3e0843f3f3d5da4523bd7a6c66e8c5d1fd` | `56d8ff58d027cfb2e9a65b97a4a979236613ccc7` |
| One-shot PAPER executor | `06191e510e353a185f41837d17b480786d014d8c` | `48d985da37e476c15c39de147b9f2131bcf69021` |
| Post-cycle audit | `1ff55e9727c74e1905891a002bad2c0bb20c014f` | `1f12938336df6f31892a06d36087d97ef03c4a31` |

For the complete sequence below, use the final reviewed source commit:

```text
1ff55e9727c74e1905891a002bad2c0bb20c014f
```

## Starting boundary

Start only after the preserved file mutation has completed and
`deploy/tools/check_manual_market_paper_preserved_post_mutation.py` has emitted
a saved report with:

- `post_mutation_audit_ready=true`;
- `runtime_files_deployed=true`;
- `manual_mode_safe=true`;
- `manual_paper_runtime_ready=true`;
- `activation_state_unchanged=true`; and
- `activation_observed=false`.

The PAPER service and timer must still be inactive.

## Common artifact paths

Use an isolated reviewed-source checkout:

```bash
set -euo pipefail

REVIEWED_REF="1ff55e9727c74e1905891a002bad2c0bb20c014f"
SRC="$(mktemp -d /var/tmp/pio-manual-paper-one-cycle-source.XXXXXX)"

PRIVATE_BUNDLE_REPORT="/var/tmp/pio-manual-paper-preserved-source-bundle.json"
PRE_MUTATION_HANDOFF="/var/tmp/pio-manual-paper-preserved-handoff.json"
EXECUTION_PRECHECK="/var/tmp/pio-manual-paper-preserved-execution-precheck.json"
MUTATION_RECEIPT="/var/tmp/pio-manual-paper-preserved-file-mutation-receipt.json"
POST_MUTATION_AUDIT="/var/tmp/pio-manual-paper-preserved-post-mutation-audit.json"

CYCLE_READINESS="/var/tmp/pio-manual-paper-one-cycle-readiness.json"
CYCLE_REQUEST="/var/tmp/pio-manual-paper-one-cycle-authorization-request.json"
CYCLE_PAYLOAD="/var/tmp/pio-manual-paper-one-cycle-authorization-payload.json"
CYCLE_SIGNING_BYTES="/var/tmp/pio-manual-paper-one-cycle-authorization.bin"
CYCLE_SIGNATURE="${CYCLE_SIGNING_BYTES}.sig"
CYCLE_AUTH_VERIFICATION="/var/tmp/pio-manual-paper-one-cycle-authorization-verification.json"
CYCLE_EXECUTION_GATE="/var/tmp/pio-manual-paper-one-cycle-execution-gate.json"
CYCLE_RECEIPT="/var/tmp/pio-manual-paper-one-cycle-execution-receipt.json"
CYCLE_POST_AUDIT="/var/tmp/pio-manual-paper-one-cycle-post-cycle-audit.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"
```

## 1. Recheck manual-cycle readiness

```bash
python3 "$SRC/deploy/tools/check_manual_market_paper_manual_cycle_readiness.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --pre-mutation-handoff "$PRE_MUTATION_HANDOFF" \
  --execution-precheck "$EXECUTION_PRECHECK" \
  --mutation-receipt "$MUTATION_RECEIPT" \
  --post-mutation-audit "$POST_MUTATION_AUDIT" \
  > "$CYCLE_READINESS"

python3 -m json.tool "$CYCLE_READINESS"
```

A ready result may report `manual_cycle_readiness_ready=true`, but it must
still report:

- `manual_cycle_execution_authorized=false`;
- `paper_timer_enable_authorized=false`;
- transaction signing/submission authorization false; and
- `live_capital_authorized=false`.

## 2. Build the exact one-cycle authorization request

Choose a dedicated virtual PAPER account and a unique run ID.

The first proof is intentionally bounded to:

- at most one new virtual position;
- at most 10 pools considered;
- `scheduler_max_positions=1`;
- `bin_array_radius=0`;
- fresh runtime time rather than a supplied `observed_at`; and
- the explicit virtual-capital/network-cost values reviewed by the human
  approver.

Example:

```bash
PAPER_ACCOUNT="<REVIEWED_VIRTUAL_ACCOUNT>"
RUN_ID="<UNIQUE_ONE_CYCLE_RUN_ID>"
CAPITAL_PER_POSITION_QUOTE="<REVIEWED_VIRTUAL_QUOTE_AMOUNT>"
NETWORK_COST_QUOTE="<REVIEWED_MODELED_NETWORK_COST>"

python3 "$SRC/deploy/tools/build_manual_market_paper_manual_cycle_authorization_request.py" \
  --source-tree "$SRC" \
  --readiness "$CYCLE_READINESS" \
  --account "$PAPER_ACCOUNT" \
  --run-id "$RUN_ID" \
  --capital-per-position-quote "$CAPITAL_PER_POSITION_QUOTE" \
  --network-cost-quote "$NETWORK_COST_QUOTE" \
  --max-pools-considered 10 \
  > "$CYCLE_REQUEST"

python3 -m json.tool "$CYCLE_REQUEST"
```

The request is review material only. It must contain:

- `authorization_request_ready=true`;
- `one_cycle_only=true`;
- `paper_only=true`;
- `manual_cycle_authorization_present=false`; and
- `manual_cycle_execution_authorized=false`.

## 3. Build and review the short-lived human-authorization payload

```bash
APPROVER_PRINCIPAL="<EXACT_ALLOWED_SIGNERS_PRINCIPAL>"

python3 "$SRC/deploy/tools/build_manual_market_paper_manual_cycle_signed_authorization.py" \
  build-payload \
  --source-tree "$SRC" \
  --request "$CYCLE_REQUEST" \
  --approver-principal "$APPROVER_PRINCIPAL" \
  --ttl-seconds 300 \
  > "$CYCLE_PAYLOAD"

python3 -m json.tool "$CYCLE_PAYLOAD"
```

The payload binds the exact request, readiness digest, parameter digest,
account, run ID, issuance, and expiry.

Its SSH signature namespace is:

```text
pio-manual-market-paper-one-cycle-authorization-v1
```

This namespace is deliberately different from the preserved-file mutation
authorization namespace.

## 4. Emit and sign the exact bytes

```bash
python3 "$SRC/deploy/tools/build_manual_market_paper_manual_cycle_signed_authorization.py" \
  emit-signing-bytes \
  --source-tree "$SRC" \
  --request "$CYCLE_REQUEST" \
  --payload "$CYCLE_PAYLOAD" \
  > "$CYCLE_SIGNING_BYTES"
```

On the human-controlled signing environment:

```bash
SSH_PRIVATE_KEY="/secure/private/path/pio-one-cycle-approval-ed25519"

ssh-keygen -Y sign \
  -f "$SSH_PRIVATE_KEY" \
  -n "pio-manual-market-paper-one-cycle-authorization-v1" \
  "$CYCLE_SIGNING_BYTES"

test -f "$CYCLE_SIGNATURE"
```

The private key is not a Pio artifact and must remain outside the repository
and shared temporary storage.

## 5. Verify the signed human authorization

```bash
ALLOWED_SIGNERS="/secure/trust/pio-one-cycle-allowed-signers"
EXPECTED_ALLOWED_SIGNERS_SHA256="<OUT_OF_BAND_PINNED_SHA256>"

printf '%s  %s\n' "$EXPECTED_ALLOWED_SIGNERS_SHA256" "$ALLOWED_SIGNERS" \
  | sha256sum -c -

python3 "$SRC/deploy/tools/build_manual_market_paper_manual_cycle_signed_authorization.py" \
  verify \
  --source-tree "$SRC" \
  --request "$CYCLE_REQUEST" \
  --payload "$CYCLE_PAYLOAD" \
  --signature "$CYCLE_SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$CYCLE_AUTH_VERIFICATION"

python3 -m json.tool "$CYCLE_AUTH_VERIFICATION"
```

A verified result may report
`human_cycle_authorization_verified=true`, but still reports
`manual_cycle_execution_authorized=false`.

## 6. Build the final fresh execution gate

```bash
python3 "$SRC/deploy/tools/check_manual_market_paper_manual_cycle_execution_gate.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --pre-mutation-handoff "$PRE_MUTATION_HANDOFF" \
  --execution-precheck "$EXECUTION_PRECHECK" \
  --mutation-receipt "$MUTATION_RECEIPT" \
  --post-mutation-audit "$POST_MUTATION_AUDIT" \
  --manual-cycle-readiness "$CYCLE_READINESS" \
  --authorization-request "$CYCLE_REQUEST" \
  --signed-authorization-verification "$CYCLE_AUTH_VERIFICATION" \
  --signed-payload "$CYCLE_PAYLOAD" \
  --signature "$CYCLE_SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$CYCLE_EXECUTION_GATE"

python3 -m json.tool "$CYCLE_EXECUTION_GATE"
```

The gate re-runs current production readiness and re-verifies the still-valid
human signature. It requires exact equality with the saved readiness and signed
verification.

A ready result contains
`manual_cycle_execution_gate_ready=true`, but the gate itself remains
read-only.

Record its exact digest:

```bash
EXPECTED_CYCLE_GATE_SHA256="<execution_gate_sha256 FROM REVIEWED GATE>"
```

## 7. Execute exactly one bounded PAPER cycle

This is the only step in this runbook that intentionally writes runtime state,
and that write is limited to the virtual PAPER database.

```bash
python3 "$SRC/deploy/tools/run_manual_market_paper_one_cycle.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --expected-execution-gate-sha256 "$EXPECTED_CYCLE_GATE_SHA256" \
  --execution-gate "$CYCLE_EXECUTION_GATE" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --pre-mutation-handoff "$PRE_MUTATION_HANDOFF" \
  --execution-precheck "$EXECUTION_PRECHECK" \
  --mutation-receipt "$MUTATION_RECEIPT" \
  --post-mutation-audit "$POST_MUTATION_AUDIT" \
  --manual-cycle-readiness "$CYCLE_READINESS" \
  --authorization-request "$CYCLE_REQUEST" \
  --signed-authorization-verification "$CYCLE_AUTH_VERIFICATION" \
  --signed-payload "$CYCLE_PAYLOAD" \
  --signature "$CYCLE_SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$CYCLE_RECEIPT"

python3 -m json.tool "$CYCLE_RECEIPT"
```

The executor:

- acquires an exclusive one-cycle lock;
- re-runs the entire gate immediately before execution;
- requires the exact expected gate digest;
- invokes only the reviewed manual PAPER module;
- uses `/opt/pio/data/pio.db`;
- performs no automatic retry;
- allows at most one newly opened virtual position;
- performs no systemd, Git, detector-cursor, signing/submission, or live-capital
  action; and
- emits a receipt with `requires_post_cycle_audit=true`.

## 8. Run the post-cycle audit immediately

```bash
python3 "$SRC/deploy/tools/check_manual_market_paper_one_cycle_post_cycle.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --pre-mutation-handoff "$PRE_MUTATION_HANDOFF" \
  --execution-precheck "$EXECUTION_PRECHECK" \
  --mutation-receipt "$MUTATION_RECEIPT" \
  --saved-post-mutation-audit "$POST_MUTATION_AUDIT" \
  --one-cycle-receipt "$CYCLE_RECEIPT" \
  > "$CYCLE_POST_AUDIT"

python3 -m json.tool "$CYCLE_POST_AUDIT"
```

The audit requires:

- the one-cycle receipt to validate;
- the production activation-state audit to remain exactly unchanged;
- no timer/service/cursor activation;
- the virtual PAPER account to remain readable;
- the PAPER event ledger to reconcile with zero position failures/reasons; and
- any position opened by the proof cycle to remain reflected in account state.

## Final stop boundary

Stop after a ready post-cycle audit.

A successful report with `post_cycle_audit_ready=true` is evidence that one
bounded manual PAPER proof cycle completed and that the PAPER ledger remained
consistent while automation stayed inactive.

It does **not** authorize:

- enabling `pio-paper@<account>.timer`;
- starting/restarting the PAPER service;
- changing detector/watcher services or cursors;
- repeating the cycle automatically;
- signing or submitting a transaction; or
- using live capital.

Recurring PAPER automation must remain a later, separately reviewed promotion
decision based on the completed proof-cycle evidence.
