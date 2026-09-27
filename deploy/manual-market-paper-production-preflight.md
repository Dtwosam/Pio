# Manual market/PAPER production preflight

This runbook is intentionally read-only. It does **not** authorize or perform a
deployment, service restart, timer enablement, detector cursor movement, wallet
action, transaction signing/submission, or real-capital action.

Reviewed source head for this preflight:

```text
b4f1dce4e7f03423f416fee53f08618292f65d00
```

## 1. Build an isolated reviewed source tree

Run this outside `/opt/pio`. Never pull, checkout, reset, restore, or clean the
production working tree.

```bash
set -euo pipefail

REVIEWED_REF="b4f1dce4e7f03423f416fee53f08618292f65d00"
SRC="/var/tmp/pio-manual-paper-preflight-${REVIEWED_REF:0:12}"

rm -rf "$SRC"
git clone https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"
```

The readiness checker independently pins the reviewed guard, manifests, and
state-reader patch blobs before loading them.

## 2. Choose the intended virtual PAPER account identifier

This is only a name for checking that the matching systemd PAPER service/timer
are inactive. The command below does not create the account.

Set the intended virtual account identifier explicitly before running the
checker:

```bash
: "${PAPER_ACCOUNT:?Set PAPER_ACCOUNT to the intended virtual paper account id}"
```

The runbook intentionally does not choose an account name for production.

## 3. Run the read-only production checker

```bash
python3 "$SRC/deploy/tools/check_manual_market_paper_readiness.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --paper-account "$PAPER_ACCOUNT"
```

The checker only:

- reads the production Git HEAD and tracked-change count;
- reads the retained detector cursor JSON without writing it;
- asks systemd whether the detector/watcher and named PAPER units are active;
- runs the selective Phase-2 shared-store prerequisite preflight;
- runs the dedicated state-reader guard with `apply=False`;
- runs the locked runtime-only market/PAPER overlay preflight.

## 4. Interpret the report

- `deployment_preflight_clean=true`: the shared prerequisite, state reader, and
  runtime-only layers are all conflict-free against their own reviewed bases.
  This does **not** mean anything was deployed.
- `runtime_files_deployed=true`: the exact Phase-2 shared prerequisites, state reader, and
  market/PAPER runtime files are already at their reviewed targets.
- `manual_mode_safe=true`: the named PAPER service and timer both explicitly
  report `inactive`.
- `manual_paper_runtime_ready=true`: the reviewed runtime is already deployed,
  detector/watcher services are active, and the named PAPER units are inactive.
- `mutation_authorized=false`: the checker never authorizes mutation.

A nonzero exit means the deployment preflight is not clean. Stop and inspect the
reported path/status categories. Do not work around a conflict with `git
checkout`, `git reset`, `git restore`, `git clean`, a service restart, or
manual detector-cursor edits.

## 5. Stop boundary

Even if the report is clean, do not create a PAPER account, apply an overlay,
enable a timer, or run the manual market/PAPER cycle from this runbook. Those
actions require a separate reviewed step after the production conflict map is
known.
