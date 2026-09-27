# Manual market/PAPER activation preflight

This runbook is intentionally **preflight-only**. The market/PAPER runtime
manifest is not authorized for production apply yet.

## Safety boundaries

- Do not restart `pio-phase2-add-detector.service` or
  `pio-phase2-prestate-watch.service`.
- Do not move or edit any Phase-2 cursor.
- Do not run `git pull`, `git checkout`, `git reset`, or `git clean`
  over `/opt/pio`.
- Do not use real capital, a wallet, signing, or transaction submission.
- Do not enable a PAPER timer before a reviewed manual cycle succeeds.

## Source and prerequisite lineage

The preflight source tree must be a clean tree containing the exact target
blobs from `deploy/manifests/market-paper-runtime.json`.

The selective `market-paper-phase2-prerequisites.json` overlay is the only
Phase-2 file prerequisite for this runtime. It contains `research_store.py` and
`storage.py` with the exact reviewed Phase-2 base/target lineage, while
intentionally excluding unrelated production-local surfaces such as `cli.py`.
The state reader remains on its separate guarded patch path.

## Read-only production preflight

From a clean reviewed source tree:

```bash
SOURCE=/var/tmp/pio-market-paper-source
python3 "$SOURCE/deploy/tools/apply_phase2_collection_stack.py" \
  --repo /opt/pio \
  --source-tree "$SOURCE" \
  --manifest "$SOURCE/deploy/manifests/market-paper-runtime.json"
```

Do **not** add `--apply`. The manifest is apply-locked and the existing tool
also rejects applying non-default manifests.

A ready report may contain only `ALREADY_TARGET`, `READY_CREATE`, or
`READY_UPDATE`. Any `CONFLICT_*`, `SOURCE_*`, symlink, missing-file, or
blob mismatch status stops the deployment path for review.

## Future manual PAPER proof cycle

Only after a separately reviewed/authorized deployment:

1. Inspect or create a virtual account explicitly. The existing production CLI
   already supports:
   `paper-create-account --account <id> --cash <virtual-cash>`.
2. Run the module directly; no `pyproject.toml` deployment is required:
   ```bash
   /opt/pio/python-learner/.venv/bin/python -m \
     meteora_learner.manual_market_paper_cycle_cli \
     --account <id> \
     --run-id <unique-run-id> \
     --capital-per-position <virtual-quote> \
     --network-cost-quote <modeled-quote> \
     --max-new-positions 1 \
     --max-pools-considered 10
   ```
3. Review market-research stage status, scheduler status, candidate/entry
   results, PAPER account equity/PnL, and the next-tick pending count.
4. Keep automation disabled until the manual cycle is clean and repeatable.

New entries are fail-closed unless market research is complete, scheduler
health is explicitly safe, the scheduler lease is free, and historical quote
data does not look ahead past each decision snapshot.
