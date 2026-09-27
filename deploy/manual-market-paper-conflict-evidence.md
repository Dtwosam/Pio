# Manual market/PAPER conflict evidence

Use this only when the manual market/PAPER production readiness check is
**not** clean.

This path is diagnostic and read-only. It does not apply an overlay, change
`/opt/pio`, restart services, enable a timer, move a detector cursor, sign or
submit a transaction, or authorize production mutation.

Reviewed source head for the current conflict/reconciliation diagnostics:

```text
affe01eafdf01d7ca8ea098900b2de7890a62ec0
```

## Why this exists

A blocked readiness result can mean a production file no longer matches either
the reviewed base or reviewed target. The production tree may contain important
local fixes, so the correct response is to identify those bytes, not overwrite
them.

The collector records hashes and metadata only. It does not print production
source contents.

It also diagnoses Git metadata reads. If a normal read fails because Git rejects
the repository ownership, it may retry that same **read-only command** with a
per-command `safe.directory` override. It never changes global or repository
Git configuration.

## Run the collector

Build an isolated reviewed source outside `/opt/pio`:

~~~bash
set -euo pipefail

REVIEWED_REF="affe01eafdf01d7ca8ea098900b2de7890a62ec0"
SRC="$(mktemp -d /var/tmp/pio-conflict-source.XXXXXX)"
EVIDENCE="/var/tmp/pio-manual-paper-conflict-evidence.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/collect_manual_market_paper_conflict_evidence.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  > "$EVIDENCE"

python3 -m json.tool "$EVIDENCE"
~~~

This collector does not require a PAPER account identifier because it is not
checking whether a named PAPER systemd unit is safe to activate. A real PAPER
account identifier is still required before the normal readiness/handoff path
can proceed.

## What it records

For the Phase-2 shared prerequisites and the runtime overlay it records:

- path and preflight status;
- expected base Git blob;
- reviewed target Git blob;
- reviewed source Git blob;
- current production Git blob;
- SHA-256 and byte size of current/source regular files.

For the dedicated state reader it records:

- current production blob/SHA-256/size;
- reviewed base and target blobs;
- reviewed target digest metadata;
- reviewed patch SHA-256 and patch scope;
- whether the current file is already target, ready for the reviewed patch, or
  conflicts.

It also records:

- detector/watcher active state;
- the retained target-pool cursor, read only;
- production HEAD and tracked-change count when readable;
- categorized Git read failures;
- whether a per-command `safe.directory` override recovered those reads;
- a canonical `evidence_sha256` sealing the report.

## Three-way reconciliation analysis

After the sealed conflict evidence identifies the exact production-local blobs,
run the read-only reconciliation analyzer from the same reviewed source tree:

~~~bash
RECONCILIATION="/var/tmp/pio-manual-paper-conflict-reconciliation.json"

python3 "$SRC/deploy/tools/analyze_manual_market_paper_conflict_reconciliation.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  > "$RECONCILIATION"

python3 -m json.tool "$RECONCILIATION"
~~~

The analyzer is bound to the reviewed production baseline
`ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8`. For only
`research_store.py` and `state_reader.rs`, it compares:

1. the exact baseline bytes from production HEAD;
2. the current production-local bytes;
3. the reviewed target bytes.

It uses `git merge-file -p --diff3` on temporary files only. Git's positive
`merge-file` return values are conflict counts (up to 127), so any such value
is reported as `CONFLICT`; only a true execution error fails the analyzer. The
report emits local/reviewed patch hashes and statistics, `CLEAN` or `CONFLICT`
merge status, and a candidate merged blob/SHA-256 when clean. It deliberately emits
no candidate source contents and never writes a merged candidate to
`/opt/pio`.

Even `all_merge_clean=true` requires manual candidate review before any
production change.

## Current live reconciliation branch

The sealed production reconciliation evidence captured on the current live
baseline established:

- `research_store.py` merges cleanly while preserving the production-local
  51-line addition. Its merged candidate Git blob is
  `31bd88e3d74490f5d0b617ff4b36383e7e12e18f`. This is evidence only; the
  candidate has not been written to production.
- `state_reader.rs` has exactly two overlapping diff3 conflict regions between
  the production-local blob
  `d1267db6708b91bc8cacabffcd397a380866c79a` and reviewed target
  `30d1435af1329bca07f73d6639539b43503e84e9`.

Only the Rust overlap regions need further content-level review.

## Collect only the two state-reader conflict hunks

Use a fresh isolated source at the reviewed commit above:

~~~bash
HUNKS="/var/tmp/pio-manual-paper-state-reader-conflict-hunks.json"

python3 "$SRC/deploy/tools/collect_manual_market_paper_state_reader_conflict_hunks.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  > "$HUNKS"

python3 -m json.tool "$HUNKS"
~~~

The collector fails closed unless all of these still match the sealed live
evidence:

- production HEAD
  `ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8`;
- production-local state-reader blob
  `d1267db6708b91bc8cacabffcd397a380866c79a`;
- the reviewed state-reader base and target blobs;
- exactly two diff3 conflict blocks.

It emits only those two conflict blocks, separated into
`production_local`, `reviewed_base`, and `reviewed_target` sections with
exact source line ranges and SHA-256 identities. Output is bounded and the
collector refuses to emit broad file contents. Its only file writes are three
temporary merge inputs inside a temporary directory; it never writes
`/opt/pio`.

Those two conflict blocks are the next evidence required before constructing a
reviewable preserved-fix candidate for `state_reader.rs`.

## Build the preserved state-reader candidate outside production

The two current overlap blocks have been reviewed:

- conflict 1 is only a production formatting variant of code that the reviewed
  target removes;
- conflict 2 contains the same state-record fields and values on the
  production-local/base sides, while the reviewed target retains those fields
  with its reviewed formatting/layout.

The reviewed resolution policy is therefore `REVIEWED_TARGET` for both exact
conflict hashes. All non-overlapping production-local changes remain preserved
through Git's automatic three-way merge.

Construct the candidate only under `/var/tmp`:

~~~bash
STAMP="$(date +%Y%m%d%H%M%S)"
CANDIDATE="/var/tmp/pio-state-reader-preserved-candidate.$STAMP.rs"
CANDIDATE_REPORT="/var/tmp/pio-state-reader-preserved-candidate.$STAMP.json"

python3 "$SRC/deploy/tools/build_manual_market_paper_state_reader_candidate.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --output "$CANDIDATE" \
  > "$CANDIDATE_REPORT"

python3 -m json.tool "$CANDIDATE_REPORT"
~~~

The constructor re-runs and validates the exact sealed hunk evidence
`805d5e89ecc9d14157a58e5fc40e1cf3b9632b6636fc61580f157890eb4736a7`,
including production HEAD, production-local state-reader blob, reviewed target,
both conflict hashes, and every conflict-side hash. It fails closed on any
drift. It resolves only those two conflicts, writes exactly one candidate file
under `/var/tmp`, verifies its Git blob, and keeps production mutation
unauthorized.

## Validate the candidate in an isolated source copy

Validation has no production repository argument and no `/opt/pio` path.
It verifies the sealed candidate file/report, copies the reviewed source into a
temporary workspace under `/var/tmp`, installs the candidate only there, and
runs Rust tests with one Cargo build job:

~~~bash
VALIDATION_REPORT="/var/tmp/pio-state-reader-candidate-validation.$STAMP.json"

python3 "$SRC/deploy/tools/validate_manual_market_paper_state_reader_candidate.py" \
  --source-tree "$SRC" \
  --candidate-report "$CANDIDATE_REPORT" \
  > "$VALIDATION_REPORT"

python3 -m json.tool "$VALIDATION_REPORT"
~~~

The isolated validator runs:

1. `cargo test --quiet`;
2. `cargo test --quiet --features live-submit`.

It records command identities, return codes, and stdout/stderr hashes and sizes,
not the build logs themselves. `validation_ready=true` requires both commands
to pass. Even a passing validation remains non-authorizing and requires a
separate production-mutation review.

If Cargo is not available in PATH, the validator also checks the existing
read-only locations `~/.cargo/bin/cargo`, `/usr/bin/cargo`,
`/usr/local/bin/cargo`, and `/usr/local/cargo/bin/cargo`. Missing Cargo is
sealed as `validation_blocker=CARGO_NOT_FOUND` rather than raising a traceback.
No Rust toolchain is installed or modified.

An already-installed executable outside those locations may be selected
explicitly with `--cargo-bin /absolute/path/to/cargo`. The executable is used
only inside the temporary validation workspace.

If a candidate was already constructed successfully, do **not** rebuild it just
because validation was blocked on Cargo. Reuse its sealed candidate report and
candidate file with a fresh reviewed source checkout, for example:

~~~bash
REVIEWED_REF="affe01eafdf01d7ca8ea098900b2de7890a62ec0"
SRC="$(mktemp -d /var/tmp/pio-candidate-validation-source.XXXXXX)"
CANDIDATE_REPORT="/var/tmp/pio-state-reader-preserved-candidate.20260927184020.json"
VALIDATION_REPORT="/var/tmp/pio-state-reader-candidate-validation.retry.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

set +e
python3 "$SRC/deploy/tools/validate_manual_market_paper_state_reader_candidate.py" \
  --source-tree "$SRC" \
  --candidate-report "$CANDIDATE_REPORT" \
  > "$VALIDATION_REPORT"
VALIDATION_RC=$?
set -e

python3 -m json.tool "$VALIDATION_REPORT"
printf 'validation_exit_code: %s\n' "$VALIDATION_RC"
~~~

If the sealed report says `CARGO_NOT_FOUND`, inspect for an existing Cargo
binary before considering any host change. Do not install a toolchain merely to
make the gate pass.

## Export the sealed candidate as a bounded portable patch

If the production host has no usable Rust toolchain, the already-sealed
candidate can be exported as a small unified patch against the reviewed
state-reader target. This exporter is production-blind: it has no `--repo`
argument and never reads or writes `/opt/pio`.

For the currently sealed candidate, reuse:

- candidate report:
  `/var/tmp/pio-state-reader-preserved-candidate.20260927184020.json`;
- candidate Git blob:
  `f54a1021cf8f89d285bde957d1f72d81857ec2fa`;
- candidate SHA-256:
  `584aed6e7ec92723e2d73220979e83bbe7fbcf7e19d50b52d05854ce80c33826`.

Export only the candidate-vs-reviewed-target delta:

~~~bash
REVIEWED_REF="affe01eafdf01d7ca8ea098900b2de7890a62ec0"
SRC="$(mktemp -d /var/tmp/pio-portable-patch-source.XXXXXX)"
CANDIDATE_REPORT="/var/tmp/pio-state-reader-preserved-candidate.20260927184020.json"
PATCH="/var/tmp/pio-state-reader-preserved-candidate.patch"
PATCH_REPORT="/var/tmp/pio-state-reader-preserved-candidate.patch.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

rm -f "$PATCH" "$PATCH_REPORT"

python3 "$SRC/deploy/tools/export_manual_market_paper_state_reader_candidate_patch.py" \
  --source-tree "$SRC" \
  --candidate-report "$CANDIDATE_REPORT" \
  --patch-output "$PATCH" \
  > "$PATCH_REPORT"

python3 -m json.tool "$PATCH_REPORT"
~~~

The exporter requires the exact sealed candidate report identity, candidate Git
blob, candidate SHA-256, candidate size, and reviewed target blob. It refuses
patches larger than 16 KiB, more than 20 hunks, or more than 256 changed lines.
It writes only one new patch file under `/var/tmp`.

The expected current delta is seven hunks with 14 additions and 28 removals.
The exact patch SHA-256 is intentionally learned from the exported artifact
rather than guessed ahead of time.

This patch is suitable for off-host validation of the exact candidate. Do not
publish it into normal public repository history solely to obtain CI; it contains
preserved production-local source changes.

## Stop boundary

Do not run the handoff, deployment-plan, deployment-gate, mutation-review, or
any apply path while the readiness preflight is blocked.

Do not resolve a reported conflict with `git checkout`, `git reset`,
`git restore`, `git clean`, a pull over `/opt/pio`, a detector/watcher
restart, or a cursor edit.

Review the exact conflict hashes first. If they match known reviewed source
lineage, that lineage can be reconciled explicitly. If they are genuinely
production-local changes, they must be reviewed and preserved or deliberately
ported before the normal read-only readiness chain can resume.
