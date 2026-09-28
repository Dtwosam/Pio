# Preserved manual market/PAPER signed human authorization

This stage authenticates an explicit human authorization for the exact
non-authorizing preserved mutation request.

Reviewed signed-authorization source:

```text
4c1c0ec41f1bcc0412402f52d06639a62a247792
```

Reviewed signed-authorization tool blob:

```text
948975c6c9244d553c3f36b5514195e354ca554d
```

The stage verifies human authorization evidence only. It does **not** make
production mutation executable.

## Trust boundary

Human authorization uses OpenSSH detached signatures with namespace:

```text
pio-manual-market-paper-preserved-authorization-v1
```

The verifier requires all of these independently:

- the exact reviewed human-authorization request;
- the exact readable signed payload;
- the detached SSH signature;
- an externally provisioned `allowed_signers` trust file;
- the expected SHA-256 of that trust file supplied from an independent trusted
  channel; and
- a named approver principal that matches the trust policy.

Do not derive the expected trust-root SHA-256 from the same
`allowed_signers` file during the approval ceremony. That would only prove the
file matches itself.

The private signing key is not a Pio artifact. Keep it outside the repository,
outside the reviewed source checkout, and outside shared temporary storage. The
Pio tool never receives the private-key path and never signs on behalf of the
human approver.

## Inputs

Start only from the exact request produced by
`build_manual_market_paper_preserved_human_authorization_request.py`.

A valid request still contains:

- `approval_artifact_present=false`;
- `authorization_granted=false`;
- `fresh_execution_recheck_required=true`;
- `mutation_authorized=false`; and
- all service/cursor/timer/capital authorization flags false.

## Build the readable short-lived payload

Use one isolated reviewed-source checkout:

```bash
set -euo pipefail

REVIEWED_REF="4c1c0ec41f1bcc0412402f52d06639a62a247792"
SRC="$(mktemp -d /var/tmp/pio-preserved-signed-auth-source.XXXXXX)"

AUTH_REQUEST="/var/tmp/pio-manual-paper-preserved-human-authorization-request.json"
AUTH_PAYLOAD="/var/tmp/pio-manual-paper-preserved-signed-human-authorization-payload.json"
SIGNING_BYTES="/var/tmp/pio-manual-paper-preserved-signed-human-authorization.bin"

APPROVER_PRINCIPAL="<EXACT_ALLOWED_SIGNERS_PRINCIPAL>"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_signed_human_authorization.py" \
  build-payload \
  --source-tree "$SRC" \
  --request "$AUTH_REQUEST" \
  --approver-principal "$APPROVER_PRINCIPAL" \
  --ttl-seconds 300 \
  > "$AUTH_PAYLOAD"

python3 -m json.tool "$AUTH_PAYLOAD"
```

The default lifetime is five minutes. The tool rejects lifetimes above fifteen
minutes.

Review the readable payload before signing. It binds:

- `request_sha256`;
- `authorization_review_sha256`;
- production-repository identity;
- operation count;
- SHA-256 of the full reviewed operation list;
- excluded scopes;
- named approver principal;
- approval ID;
- issuance and expiry; and
- the exact decision `AUTHORIZE_EXACT_PRESERVED_FILE_MUTATIONS`.

The payload still contains `execution_ready=false` and
`mutation_authorized=false`.

## Emit the exact bytes to sign

Do not sign the pretty-printed JSON file directly.

After reviewing it, derive the exact canonical bytes consumed by the verifier:

```bash
python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_signed_human_authorization.py" \
  emit-signing-bytes \
  --source-tree "$SRC" \
  --request "$AUTH_REQUEST" \
  --payload "$AUTH_PAYLOAD" \
  > "$SIGNING_BYTES"
```

This command revalidates both the request and payload and emits no trailing
newline.

## Human detached signature

On the human-controlled signing environment, with the private key kept outside
Pio:

```bash
SSH_PRIVATE_KEY="/secure/private/path/pio-approval-ed25519"

ssh-keygen -Y sign \
  -f "$SSH_PRIVATE_KEY" \
  -n "pio-manual-market-paper-preserved-authorization-v1" \
  "$SIGNING_BYTES"

SIGNATURE="${SIGNING_BYTES}.sig"
test -f "$SIGNATURE"
```

The signature covers exactly the canonical bytes emitted above.

## Verify the signed authorization

The public trust file and its expected digest are separate trust material:

```bash
ALLOWED_SIGNERS="/secure/trust/pio-preserved-allowed-signers"
EXPECTED_ALLOWED_SIGNERS_SHA256="<OUT_OF_BAND_PINNED_SHA256>"
SIGNATURE="${SIGNING_BYTES}.sig"
AUTH_VERIFICATION="/var/tmp/pio-manual-paper-preserved-signed-human-authorization-verification.json"

printf '%s  %s\n' "$EXPECTED_ALLOWED_SIGNERS_SHA256" "$ALLOWED_SIGNERS" \
  | sha256sum -c -

python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_signed_human_authorization.py" \
  verify \
  --source-tree "$SRC" \
  --request "$AUTH_REQUEST" \
  --payload "$AUTH_PAYLOAD" \
  --signature "$SIGNATURE" \
  --allowed-signers "$ALLOWED_SIGNERS" \
  --expected-allowed-signers-sha256 "$EXPECTED_ALLOWED_SIGNERS_SHA256" \
  > "$AUTH_VERIFICATION"

python3 -m json.tool "$AUTH_VERIFICATION"
```

The verifier fails closed if the request or payload is invalid, the trust-root
digest differs, the principal is not accepted by `allowed_signers`, the
signature is invalid, the payload is not yet valid, or the payload has expired.

## Verified result

A successful report can contain:

- `human_authorization_verified=true`;
- `signature_verified=true`;
- `trust_root_digest_matches=true`; and
- `approval_not_expired=true`.

That means the named trusted principal signed the exact short-lived payload
bound to the exact preserved mutation request.

It still must contain:

- `fresh_execution_recheck_required=true`;
- `execution_ready=false`;
- `production_deployment_authorized=false`;
- `mutation_authorized=false`;
- service restart authorization false;
- detector cursor movement authorization false;
- PAPER timer enablement authorization false; and
- live-capital authorization false.

## Stop boundary

Stop here.

A verified human signature is not an execution permit. It is authenticated
authorization evidence that must be consumed by a separately reviewed
execution-time gate.

The next safe implementation must bind this exact
`verification_sha256` to the original request/review lineage, re-check the
approval validity window, re-read the rollback material, and immediately
re-check every production operation against its exact expected-current state
before any write can become actionable.

No production file, Git state, service, detector cursor, PAPER timer,
transaction, or capital is changed by this runbook.
