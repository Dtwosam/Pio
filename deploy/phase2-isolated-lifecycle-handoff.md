# Phase 2 isolated lifecycle handoff

Use `deploy/tools/check_phase2_isolated_lifecycle_handoff.py` when the local
Phase-2 machine state is unclear and you want one zero-RPC answer for the next
guarded step.

The handoff composes the existing reviewed activation, smoke, timer-readiness,
and operator-status checks. It does not replace their safety rules.

## Boundary

The handoff is read-only. It:

- makes no Solana or Jupiter RPC calls;
- performs no database writes;
- performs no systemd control;
- performs no daemon reload;
- does not move detector cursors;
- does not evaluate or perform phase promotion;
- does not touch capital.

## State progression

A normal deployment progresses through these states:

1. `SOURCE_BOOTSTRAP_REQUIRED`
   - next action: `BOOTSTRAP_PINNED_SOURCE`
   - tool: `bootstrap_phase2_isolated_source.py`
2. `SOURCE_PREPARATION_REQUIRED`
   - next action: `PREPARE_PINNED_RUNTIME`
   - tool: `prepare_phase2_isolated_runtime.py`
3. `RUNTIME_STAGING_READY`
   - next action: `STAGE_REVIEWED_RUNTIME`
   - tool: `stage_phase2_isolated_runtime.py`
4. `SYSTEMD_UNITS_NOT_READY`
   - next action: `INSTALL_REVIEWED_UNITS`
   - tool: `install_phase2_isolated_systemd_units.py`
5. `DETECTOR_ACTIVATION_READY`
   - next action: `ACTIVATE_PRESTATE_STREAMS_AND_DETECTOR`
   - tool: `activate_phase2_isolated_detector.py`
6. `SMOKE_REQUIRED`
   - next action: `RUN_ONE_SHOT_SMOKE`
   - tool: `run_phase2_isolated_smoke.py`
7. `TIMER_ACTIVATION_READY`
   - next action: `ACTIVATE_EVIDENCE_TIMER`
   - tool: `activate_phase2_isolated_timer.py`
8. `RUNNING_HEALTHY`
   - next action: `MONITOR_ZERO_RPC_STATUS`
   - tool: `check_phase2_isolated_operator_status.py`

The default build source is
`/opt/pio-phase2-build/<PINNED_SOURCE_HEAD>`. The handoff checks that source
without fetching or modifying it. A missing source produces
`SOURCE_BOOTSTRAP_REQUIRED`; an exact tracked-clean pinned source produces
`SOURCE_PREPARATION_REQUIRED`; and a fully prepared source that passes the
reviewed runtime contract produces `RUNTIME_STAGING_READY`.

If the build source exists but is neither the exact clean pin nor a fully
prepared reviewed runtime, the handoff returns
`SOURCE_CONFLICT_REVIEW_REQUIRED` rather than overwriting it.

If prerequisites are incomplete, the handoff returns a categorical blocker list
rather than guessing a mutation.

## Rate-limit recovery

When repeated provider rejection is active and the timer is already paused, the
handoff returns `RATE_LIMIT_PAUSED` and explicitly keeps the recurring timer
paused. It does not recommend a smoke or resume attempt until the active
provider incident has cleared.

If a running timer still requires the reviewed pause action, the handoff returns
`RATE_LIMIT_PAUSE_REQUIRED` and points at
`autopause_phase2_isolated_timer.py`.

A fresh one-shot smoke remains required before a paused timer can be resumed.

## Source inspection

The handoff accepts `--source-tree` to inspect a non-default side build path.
It also accepts `--repository-url` only for the bootstrap preflight's
secret-safe URL validation. The lifecycle handoff itself never fetches Git
objects. Network access remains exclusive to an explicit
`bootstrap_phase2_isolated_source.py --apply` operator action.


## Next-step parameter contract

The handoff returns two additional fields:

- `next_parameters`: only non-secret parameters expected by the reviewed next
  tool, such as source paths, runtime roots, systemd destination paths, receipt
  paths, and numeric freshness limits.
- `next_mutation_flag`: the explicit flag required to cross the next tool's
  mutation boundary, normally `--apply` or, for runtime preparation,
  `--prepare`. It is `null` for read-only review/monitoring steps.

The handoff never executes the next tool and never appends the mutation flag on
the operator's behalf.

Environment values are not included. In particular, the handoff may report the
path `/etc/pio/pio.env`, but never reads out or returns
`SOLANA_RPC_URL`, RPC credentials, or API keys.

For unit installation, `next_parameters.source_tree` resolves the staged
`current` symlink to its reviewed release directory because the unit installer
intentionally rejects a symlink as its source tree.


## Historical post-audit evidence handoff

When an immutable
`PHASE2_MUTATION_POST_AUDIT_CATALOG_SNAPSHOT_V1` artifact is available, use
`deploy/tools/check_phase2_mutation_post_audit_catalog_handoff.py` to view
historical mutation evidence and the current lifecycle state in one zero-RPC
report.

The authorities remain deliberately separate:

- the catalog snapshot proves only historical evidence lineage;
- the snapshot always remains `historical_snapshot_only=true` and
  `historical_authorizes_next_action=false`;
- `current_state`, `current_next_action`, `current_next_tool`,
  `current_next_parameters`, and `current_next_mutation_flag` come only from
  the fresh lifecycle handoff;
- `snapshot_influenced_current_action=false` is invariant;
- the combined report itself sets `authorizes_next_action=false` and
  `requires_fresh_separate_mutation_authorization=true`.

An invalid historical snapshot raises attention and an explicit historical
lineage blocker, but it does not replace or derive the current lifecycle
advice. Conversely, later lifecycle changes may change the current next step
without changing the saved historical snapshot identity.

When the current post-audit archive is locally available, pass
`--artifact-directory <DIR>` to additionally re-verify every artifact and
require the live evidence identity set to match the immutable snapshot. This
freshness check ignores artifact path relocation as identity, but it treats
missing, unexpected, unverified, duplicate, or lifecycle-metadata-changed
evidence as a blocker. The fresh verifier must also report the exact same
snapshot path and SHA-256 as the static verifier, so a snapshot changed between
the two reads fails closed. Without `--artifact-directory`, the handoff remains
a static historical snapshot check.


## Immutable combined handoff evidence

After a historical catalog handoff is verified, it may be frozen as private
evidence with
`deploy/tools/save_phase2_mutation_post_audit_catalog_handoff.py`.

The saver reruns the reviewed combined handoff, requires verified historical
lineage, preserves any current attention/blocker state, and writes a create-only
`PHASE2_MUTATION_POST_AUDIT_CATALOG_HANDOFF_SNAPSHOT_V1` artifact at mode
`0600`. The output must be outside protected production paths.

The saved handoff records the current lifecycle recommendation as historical
evidence only. Both the embedded handoff and the save report keep
`authorizes_next_action=false` and
`requires_fresh_separate_mutation_authorization=true`. A later operator action
must still come from a fresh current lifecycle/preflight/authorization chain;
the saved handoff can never be replayed as mutation authority.


Saved combined handoff artifacts can later be checked with
`deploy/tools/check_phase2_mutation_post_audit_catalog_handoff_snapshot.py`.
That verifier is static and zero-RPC: it validates the artifact schema,
canonical payload hash, recorded Git ancestry, and exact historical handoff-tool
bytes. It deliberately does **not** rerun the current lifecycle. Therefore a
verified saved handoff remains historical evidence only and still cannot
authorize the recorded next action.


## Portable archive + current lifecycle handoff

When the historical handoff has been packaged as a deterministic private
`PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_ARCHIVE_V1` tar, use
`deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py`
to verify the single archive and independently evaluate the current lifecycle
without first unpacking a persistent bundle directory.

The archive remains historical evidence only:

- archive verification rechecks deterministic tar metadata, member hashes, and
  the reconstructed portable bundle;
- `historical_archive_only=true` and
  `historical_authorizes_next_action=false`;
- every `current_*` action field comes only from the fresh
  `check_phase2_isolated_lifecycle_handoff.py` result;
- `archive_influenced_current_action=false` is invariant;
- the combined report sets `authorizes_next_action=false` and
  `requires_fresh_separate_mutation_authorization=true`.

This path is read-only and zero-RPC. It does not extract the archive into a
persistent production location, control services, write the database, execute a
mutation, or promote a phase.


## Immutable portable archive handoff snapshot

A verified portable archive + current lifecycle handoff may be frozen as private
historical evidence with
`deploy/tools/save_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py`.

The saver reruns the archive verifier and current zero-RPC lifecycle handoff,
requires the archive evidence lineage to be verified and non-authorizing, binds
the canonical handoff payload to the exact reviewed Git commit and handoff-tool
SHA-256, and writes a create-only
`PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_ARCHIVE_HANDOFF_SNAPSHOT_V1`
artifact at mode `0600` outside protected production paths.

The saved lifecycle recommendation is historical evidence only. It cannot be
replayed as current mutation authority and always keeps
`authorizes_next_action=false` and
`requires_fresh_separate_mutation_authorization=true`.

Later, use
`deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff_snapshot.py`
for static verification. The verifier rechecks the canonical payload digest,
recorded Git ancestry, exact historical archive-handoff tool bytes, evidence
lineage, and the non-authorizing boundary. It deliberately does not rerun the
current lifecycle.


## Fresh archive handoff evidence reverification

If both an immutable
`PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_ARCHIVE_HANDOFF_SNAPSHOT_V1`
snapshot and the original portable archive are available, run
`deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff_freshness.py`
to freshly reverify the archive against the saved handoff evidence.

The check compares immutable identities rather than paths:

- the saved snapshot is statically verified first;
- the supplied archive is fully reverified, including its reconstructed portable
  bundle;
- the archive SHA-256 must match the SHA recorded in the snapshot;
- the source-bundle SHA-256 must also match;
- archive relocation is explicitly ignored as evidence drift.

The recorded lifecycle recommendation is not rerun. The result remains
historical evidence only, sets `current_lifecycle_rechecked=false`, never
authorizes the recorded action, and requires fresh separate mutation
authorization.
