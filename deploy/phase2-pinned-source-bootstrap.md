# Phase 2 pinned source bootstrap

The isolated Phase-2 runtime is built from a reviewed pinned commit, not from
whatever happens to be current on `main`.

Use `deploy/tools/bootstrap_phase2_isolated_source.py` to create that exact
source tree reproducibly before preparation.

## Boundary

The bootstrap:

- never writes under `/opt/pio`;
- never writes under `/opt/pio-phase2-runtime`;
- never controls systemd;
- never reads or writes the Phase-2 database;
- never makes Solana or Jupiter RPC calls;
- may perform a Git network fetch only when `--apply` creates a missing source
  tree.

An existing exact, tracked-clean pinned source tree is reused without any
network fetch.

## Default destination

The default build source is:

`/opt/pio-phase2-build/<PINNED_SOURCE_HEAD>`

where `PINNED_SOURCE_HEAD` comes from
`check_phase2_isolated_runtime.py`.

## Reviewed sequence

Preflight:

```bash
python3 deploy/tools/bootstrap_phase2_isolated_source.py
```

Create the exact pinned source tree:

```bash
python3 deploy/tools/bootstrap_phase2_isolated_source.py --apply
```

Then run the existing preparation guard against that path:

```bash
python3 deploy/tools/prepare_phase2_isolated_runtime.py \
  --source-tree /opt/pio-phase2-build/<PINNED_SOURCE_HEAD>
```

Only after the preparation preflight is ready should the preparation tool be
run with `--prepare`, followed by the existing atomic staging tool.

The bootstrap fails closed if the destination already exists with a different
HEAD, tracked modifications, a non-Git directory, or a protected runtime path.
