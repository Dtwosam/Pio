# Manual market/PAPER preserved evidence package

This stage is the final read-only audit for the preservation-aware manual
market/PAPER deployment path.

Reviewed package source commit:

```text
0dee71978acfe93e116db559919f684ade150bb5
```

Reviewed package tool Git blob:

```text
c3c61759246857855cbf28f844b4bc3e64ad8f3b
```

The package consumes only already-saved preservation-aware artifacts:

1. preserved readiness;
2. sealed preserved handoff;
3. deterministic preserved deployment plan;
4. preserved fresh deployment gate;
5. preserved mutation review.

It does not read the production repository, run subprocesses, write files,
restart services, move the detector cursor, enable the PAPER timer, sign or
submit transactions, or authorize capital.

## Build the package

Use an isolated checkout of the reviewed source commit:

```bash
set -euo pipefail

REVIEWED_REF="0dee71978acfe93e116db559919f684ade150bb5"
SRC="$(mktemp -d /var/tmp/pio-preserved-package-source.XXXXXX)"

READINESS="/var/tmp/pio-manual-paper-preserved-readiness.json"
HANDOFF="/var/tmp/pio-manual-paper-preserved-handoff.json"
PLAN="/var/tmp/pio-manual-paper-preserved-deployment-plan.json"
GATE="/var/tmp/pio-manual-paper-preserved-deployment-gate.json"
REVIEW="/var/tmp/pio-manual-paper-preserved-mutation-review.json"
PACKAGE="/var/tmp/pio-manual-paper-preserved-evidence-package.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/build_manual_market_paper_preserved_evidence_package.py" \
  --source-tree "$SRC" \
  --readiness "$READINESS" \
  --handoff "$HANDOFF" \
  --plan "$PLAN" \
  --gate "$GATE" \
  --review "$REVIEW" \
  > "$PACKAGE"

python3 -m json.tool "$PACKAGE"
```

## Required result

A complete package requires all of the following:

- `readiness_to_handoff_bound=true`;
- `handoff_to_plan_bound=true`;
- `plan_to_gate_bound=true`;
- `gate_to_review_bound=true`;
- one identical `private_bundle_sha256` across all five stages;
- `all_stages_ready=true`;
- `fresh_gate_matches_saved=true`;
- `stale_pre_preservation_artifacts_forbidden=true`;
- `evidence_complete=true`.

The package validator recomputes the cross-stage digest bindings from the
embedded linkage digests. It does not trust the boolean summaries by
themselves.

Every authorization field must remain false. In particular,
`evidence_complete=true` is **not** deployment or mutation authorization.

## Boundary after a complete package

A complete package means only that the current preservation-aware read-only
evidence chain is internally consistent and review-ready.

It still does not capture mutation backups, perform the execution-time
expected-current recheck, copy files into production, restart services, move
the detector cursor, enable timers, sign transactions, submit transactions, or
authorize live capital.

Any production mutation remains a separate reviewed and explicitly authorized
step. Pre-preservation readiness, handoff, plan, gate, and mutation-review
artifacts are stale for this chain and must never be substituted into the
preserved package.
