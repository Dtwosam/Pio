# Manual market/PAPER preflight handoff

This step turns one clean read-only production preflight into a sealed JSON
handoff that can be checked again immediately before any separately reviewed
deployment step.

It still does **not** authorize or perform deployment, service restart, timer
enablement, detector cursor movement, wallet action, transaction submission, or
real-capital action.

## Why this exists

The production tree intentionally contains local/uncommitted fixes. A clean
preflight result is therefore not enough by itself: the relevant production
state can change between review and a later deployment.

The handoff records the exact safety-relevant preflight state and a canonical
SHA-256 digest. Verification re-runs the same read-only readiness checker and
fails closed when any bound section changes, including:

- production Git HEAD and tracked-change count;
- a SHA-256 fingerprint of the exact tracked working-tree diff;
- detector/watcher service state;
- selected PAPER service/timer state;
- retained target-pool detector cursor;
- selective Phase-2 shared-store prerequisite status;
- single-slot state-reader status;
- runtime-only manual market/PAPER overlay status;
- readiness flags.

The tracked-diff fingerprint is computed from `git diff --binary` with external
diffs and text conversion disabled. Raw diff bytes are never written into the
handoff. The tool fingerprints the tracked diff both before and after the
read-only readiness pass and fails closed if it changes during capture.

The isolated reviewed source-tree path itself is not part of the identity, so a
fresh clone of the exact reviewed source can verify the same production state.

## Capture

Use the same isolated reviewed source tree and explicit virtual PAPER account
chosen for the production readiness check.

```bash
python3 "$SRC/deploy/tools/manual_market_paper_handoff.py" capture \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --paper-account "$PAPER_ACCOUNT" \
  > /var/tmp/pio-manual-paper-preflight-handoff.json
```

Exit `0` means the snapshot itself is internally valid and the read-only
handoff conditions were true. Exit `2` means the tool still prints the
diagnostic snapshot, but one or more handoff conditions were not ready.

`handoff_ready=true` requires:

- an explicit PAPER account identifier;
- conflict-free deployment preflight;
- detector and watcher both active;
- the selected PAPER service and timer both inactive;
- `mutation_authorized=false`.

`deployment_needed=true` simply means reviewed runtime files are not already at
their targets. It is not deployment authorization.

## Verify before any later reviewed mutation

```bash
python3 "$SRC/deploy/tools/manual_market_paper_handoff.py" verify \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --paper-account "$PAPER_ACCOUNT" \
  --snapshot /var/tmp/pio-manual-paper-preflight-handoff.json
```

Verification succeeds only when:

- the saved snapshot is untampered and bound to the reviewed readiness tool;
- the saved snapshot was handoff-ready;
- the fresh current preflight is also handoff-ready;
- the full production-state digest matches exactly.

When state differs, `changed_sections` identifies the top-level areas that
changed. A `tracked_diff_sha256` change means the tracked local source content
changed even if the tracked-change count stayed identical.

Do not work around drift by checking out, resetting, cleaning, pulling over
`/opt/pio`, restarting the detector/watcher, or editing the detector cursor.
Re-review the new read-only state instead.

## Next read-only stages

After a handoff is captured, build the deterministic saved plan with
`build_manual_market_paper_deployment_plan.py`. Immediately before any future
separately reviewed mutation, run
`check_manual_market_paper_deployment_gate.py`; the gate re-runs current
production readiness and requires the current state to match this handoff and
the saved plan exactly.

## Stop boundary

This handoff is evidence for review only. It always emits
`mutation_authorized=false`. The deployment plan and fresh gate also remain
non-authorizing. Do not apply reviewed overlays, restart services, move the
detector cursor, enable PAPER automation, or use real capital from this step.
