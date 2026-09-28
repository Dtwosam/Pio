# Preserved manual market/PAPER human-authorization request

This is the first stage after the terminal machine-generated preserved
authorization review.

Reviewed request-builder source:

\`\`\`text
14b69a9566fbeeea283f0c2893eff7bf4ec6258b
\`\`\`

Reviewed request-builder blob:

\`\`\`text
f7d31b7bbd421945fd8f7ca1353832e6e89da11b
\`\`\`

The tool is non-authorizing. It does not inspect production, write production
files, grant mutation permission, restart services, move detector state, enable
the PAPER timer, sign or submit transactions, or deploy capital.

## Input

Run this only after a preserved authorization review exists and reports:

- \`authorization_review_ready=true\`;
- \`authorization_granted=false\`;
- \`requires_explicit_human_authorization=true\`;
- \`requires_fresh_execution_recheck=true\`; and
- every deployment/mutation/service/cursor/timer/capital authorization false.

The request builder validates that terminal artifact with the exactly pinned
reviewed authorization-review validator before copying any scope into the
request.

## Run

Use one isolated reviewed-source checkout:

\`\`\`bash
set -euo pipefail

REVIEWED_REF="14b69a9566fbeeea283f0c2893eff7bf4ec6258b"
SRC="$(mktemp -d /var/tmp/pio-preserved-human-auth-request-source.XXXXXX)"

AUTH_REVIEW="/var/tmp/pio-manual-paper-preserved-authorization-review.json"
AUTH_REQUEST="/var/tmp/pio-manual-paper-preserved-human-authorization-request.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_human_authorization_request.py" \
  --source-tree "$SRC" \
  --authorization-review "$AUTH_REVIEW" \
  > "$AUTH_REQUEST"

python3 -m json.tool "$AUTH_REQUEST"
\`\`\`

The tool has no \`--repo\` argument. It does not access \`/opt/pio\`.

## Ready result

A valid request is sealed as \`request_sha256\` and binds:

- the exact terminal \`authorization_review_sha256\`;
- the production-repository identity copied from that terminal review;
- the private rollback-backup directory identity;
- the exact operation index, layer, operation and path;
- each plan-operation SHA-256;
- each expected-current and target Git blob;
- each rollback operation/blob; and
- each backup path/blob/SHA-256/size/mode where a backup is required.

The authorization scope is only:

\`\`\`text
PRESERVED_FILE_MUTATIONS_ONLY
\`\`\`

The request explicitly excludes:

- service restart;
- detector cursor movement;
- PAPER timer enablement;
- transaction signing;
- transaction submission;
- live capital; and
- broad Git operations.

## Non-authorization boundary

Even when \`authorization_request_ready=true\`, the request must still contain:

- \`approval_artifact_present=false\`;
- \`authorization_granted=false\`;
- \`explicit_human_authorization_required=true\`;
- \`fresh_execution_recheck_required=true\`;
- \`requires_separate_mutation_authorization=true\`;
- \`production_file_modified=false\`;
- \`production_repository_git_mutated=false\`; and
- every deployment/mutation/service/cursor/timer/capital authorization false.

Re-hashing a modified JSON request cannot turn any of those fields into
authorization because the validator rejects the changed semantics independently
of the digest.

## Stop boundary

Stop here.

This request is material for an explicit human decision. It is not that
decision, not an approval token, and not an execution permit.

No approval-artifact producer, approval verifier, execution-time mutation gate,
or production mutation implementation is introduced by this stage.

Any future continuation must separately define and review how an explicit human
authorization is authenticated and bound to this exact
\`authorization_review_sha256\`/\`request_sha256\`. After that authorization,
the implementation must still perform an immediate per-operation
expected-current recheck before the first production write.

Broad Git operations over the production working tree remain prohibited.
