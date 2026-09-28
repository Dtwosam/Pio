# Preserved manual market/PAPER post-mutation audit

This stage is the first checkpoint after the exact-scope production file writer.

Reviewed audit source:

```text
5e6b5e430bb121edd0b859c2094fcad85b4ffa72
```

Reviewed audit tool blob:

```text
f23bae2b423fe68778c75cb1131ed5822902d5d6
```

The audit is read-only. It validates what was actually deployed and confirms
the currently observed activation state still matches the pre-mutation
handoff.

## Inputs

Use the exact artifacts from the approved mutation chain:

- private preserved-source bundle report;
- pre-mutation preserved handoff;
- final execution precheck; and
- successful preserved file-mutation receipt.

The mutation receipt must bind the same
`execution_precheck_sha256` as the saved precheck, including the same
approver principal, approval ID and approval expiry.

## Run

Use one isolated reviewed-source checkout:

```bash
set -euo pipefail

REVIEWED_REF="5e6b5e430bb121edd0b859c2094fcad85b4ffa72"
SRC="$(mktemp -d /var/tmp/pio-preserved-post-mutation-audit-source.XXXXXX)"

PRIVATE_BUNDLE_REPORT="/var/tmp/pio-manual-paper-preserved-source-bundle.json"
PRE_MUTATION_HANDOFF="/var/tmp/pio-manual-paper-preserved-handoff.json"
EXECUTION_PRECHECK="/var/tmp/pio-manual-paper-preserved-execution-precheck.json"
MUTATION_RECEIPT="/var/tmp/pio-manual-paper-preserved-file-mutation-receipt.json"
POST_MUTATION_AUDIT="/var/tmp/pio-manual-paper-preserved-post-mutation-audit.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/check_manual_market_paper_preserved_post_mutation.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --private-bundle-report "$PRIVATE_BUNDLE_REPORT" \
  --pre-mutation-handoff "$PRE_MUTATION_HANDOFF" \
  --execution-precheck "$EXECUTION_PRECHECK" \
  --mutation-receipt "$MUTATION_RECEIPT" \
  > "$POST_MUTATION_AUDIT"

python3 -m json.tool "$POST_MUTATION_AUDIT"
```

## Target and rollback verification

For every receipt operation, the audit binds the receipt back to the exact
execution-precheck operation.

It re-reads the production target with no-follow checks and requires:

- identical operation index, layer, type and path;
- identical expected-current and target blobs;
- receipt `before_blob` equal to the precheck expected-current blob;
- update mode equal to the sealed rollback/pre-mutation mode;
- create mode exactly `0644`;
- current production bytes equal to the receipt target blob; and
- current target mode equal to the receipt target mode.

For every update, the private rollback file is re-read again and must still
match its sealed Git blob, SHA-256, size, mode, and expected-current identity.

## Fresh operational observation

After file verification, the audit runs fresh preserved readiness against the
mutated production tree.

It requires the runtime files to now be fully deployed while the manual PAPER
safety boundary remains intact:

- detector service state matches the pre-mutation handoff;
- watcher service state matches the pre-mutation handoff;
- PAPER service state matches the pre-mutation handoff;
- PAPER timer state matches the pre-mutation handoff;
- production Git HEAD matches the pre-mutation handoff;
- target pool identity matches;
- target-pool cursor value matches;
- manual mode remains safe;
- operational detector/watcher services remain healthy; and
- the manual PAPER runtime now reports ready.

These comparisons are point-in-time state checks. They do not claim that a
service could never have restarted and returned to the same observed state.
The stronger guarantee that this writer itself cannot restart services comes
from the separately reviewed writer implementation, which contains no such
primitive.

## Ready result

A successful audit can report:

- `all_targets_verified=true`;
- `all_rollback_material_verified=true`;
- `runtime_files_deployed=true`;
- `manual_mode_safe=true`;
- `operational_services_healthy=true`;
- `manual_paper_runtime_ready=true`;
- `activation_state_unchanged=true`;
- `activation_observed=false`; and
- `post_mutation_audit_ready=true`.

The report is sealed as `post_mutation_audit_sha256`.

It still requires all activation authorizations false and records that the
audit itself modified no production file or Git state.

## Stop boundary

Stop here.

A ready post-mutation audit proves the exact reviewed file deployment is
present, rollback material remains available, and the currently observed
activation state has not changed.

It does not authorize a service restart, detector cursor edit, PAPER timer
enablement, transaction signing/submission, or live capital.

The safest next stage is a separately scoped request for one explicit manual
PAPER proof cycle. That future scope must remain PAPER-only and bind a single
account, run ID, virtual-capital amount, modeled network cost and bounded
cycle parameters. It must not reuse the preserved-file mutation authorization.
