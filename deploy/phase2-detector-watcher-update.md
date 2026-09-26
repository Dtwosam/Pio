# Phase 2 detector/watcher production import and update

This runbook supports GitHub Issue #6. It is an operational reproducibility procedure only. It does not authorize service restarts, detector cursor movement, phase promotion, or capital activity.

## Preconditions

- Keep the production detector and watcher running unless there is a concrete failure.
- Do not manually advance the Phase-2 detector cursor.
- Do not pull, checkout, reset, or replace the live VPS working tree.
- Treat /opt/pio as containing production-only local fixes until imported files are hash-verified.
- RPC URLs and API keys must stay out of process arguments.

## 1. Capture exact production sources

Use snapshot_phase2_production_sources.py on the VPS to capture the exact files currently used by systemd. Capture at least:

- detector=/opt/pio/scripts/phase2-add-detector.py
- watcher=/opt/pio/scripts/phase2-prestate-watch.py
- detector_unit=<exact detector unit file used by systemd>
- watcher_unit=<exact watcher unit file used by systemd>

Capture active systemd drop-ins as additional labels. Use a new output directory and never reconstruct production source from memory.

## 2. Verify before import

Run verify_phase2_production_sources.py against the capture directory. Do not edit files inside the snapshot. The snapshot must pass checksum and size verification before any source is copied into the repository.

## 3. Import exact bytes

The Issue #6 contract requires these repository paths:

- scripts/phase2-add-detector.py
- scripts/phase2-prestate-watch.py
- deploy/systemd/pio-phase2-add-detector.service
- deploy/systemd/pio-phase2-prestate-watch.service

Copy exact bytes from the reviewed production snapshot. Do not reformat or refactor during the initial import. Any cleanup must be a separate reviewed change after the exact production baseline is under source control.

## 4. Required regression coverage

The import gate requires named tests covering:

- resolved subprocess run wrapper;
- fail-closed detector cursor behavior;
- pagination reaching the retained cursor without skipping;
- RPC retry/backoff;
- 1000-signature page support;
- watcher prestate promotion;
- detector failure not stopping the watcher;
- RPC URL/API key absence from process arguments.

Required test names are defined in deploy/manifests/phase2-production-import-contract.json.

## 5. Run the fail-closed import gate

Run check_phase2_production_import_ready.py with the repository root and exact production snapshot directory.

The gate passes only when all required imports exist, every imported file matches the production snapshot hash, every required regression test function is present, and this runbook exists. A passing gate proves source-control import readiness; it does not authorize production deployment.

## 6. CI and review

Run full Rust and Python CI. Keep the import PR draft until the import gate passes against the reviewed production snapshot and all regression tests pass.

## 7. Production update after review

Do not deploy by checking out or pulling the PR over /opt/pio. For any later approved deployment:

1. re-check detector and watcher health;
2. confirm the retained detector cursor has not been manually moved;
3. compare live hashes with the reviewed baseline;
4. selectively copy only reviewed files whose production change is intended;
5. preserve environment-based RPC configuration;
6. do not restart either service unless the change requires it or there is a concrete failure;
7. verify detector and watcher independently after any authorized restart.

If live files differ unexpectedly from the reviewed baseline, stop and reconcile the divergence before applying anything.
