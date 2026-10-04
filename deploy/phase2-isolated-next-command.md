# Phase 2 isolated next-command preview

Use `deploy/tools/render_phase2_isolated_next_command.py` when you want a
copyable command for the current lifecycle handoff without manually translating
its structured parameters into CLI flags.

The renderer calls the zero-RPC lifecycle handoff and renders only the next
tool's **read-only preflight command**.

## Mutation boundary

If the next reviewed step has a mutation mode, the renderer reports it in
`mutation_flag`:

- `--apply` for bootstrap, staging, systemd installation or exact-predecessor
  upgrade, activation, smoke, timer activation, or autopause;
- `--prepare` for isolated runtime preparation.

The mutation flag is never appended to `preflight_argv` or
`preflight_command`. The renderer never executes either command.


For `SYSTEMD_UNIT_UPGRADE_READY`, the rendered preflight invokes
`upgrade_phase2_isolated_systemd_units.py` without `--apply`. If installed
unit bytes are modified or otherwise outside the reviewed predecessor/current
contract, the lifecycle returns `SYSTEMD_UNIT_CONFLICT_REVIEW_REQUIRED`
instead and no mutation command is rendered.

That means the output can be used to inspect the next gate without silently
crossing into a Git fetch, build mutation, systemd action, RPC smoke cycle, or
timer change.

## Secret safety

The renderer uses the lifecycle handoff's already-sanitized
`next_parameters`. It may include paths such as `/etc/pio/pio.env`, but it
does not read or return environment values, `SOLANA_RPC_URL`, RPC credentials,
or API keys.

Arguments are rendered with shell-safe quoting. The next tool must be a regular
reviewed file directly under `deploy/tools`; path traversal and symlinked tools
are rejected.

## Example

```bash
python3 deploy/tools/render_phase2_isolated_next_command.py
```

The JSON output includes:

- lifecycle `state` and `next_action`;
- `preflight_argv`, suitable for programmatic execution;
- `preflight_command`, suitable for copy/paste review;
- `mutation_flag`, shown separately;
- the complete underlying lifecycle report for auditability.


## Guarded preflight execution

Use `deploy/tools/run_phase2_isolated_next_preflight.py` when you want Pio to
execute the rendered preflight instead of copying it manually.

This runner:

- executes only an allowlisted reviewed tool under `deploy/tools`;
- forces the current Python interpreter and never invokes a shell;
- refuses `--apply` and `--prepare` if either appears in the executed argv;
- removes Solana/Jupiter/Helius credentials from the child process environment;
- captures only structured JSON output and never returns raw child stderr;
- rejects a result that reports RPC, database, systemd, daemon-reload,
  production-tree, build, apply, or prepare mutation.

A non-zero preflight remains inspectable as structured JSON but is not treated
as ready for mutation.

## Reviewed mutation command rendering

Use `deploy/tools/render_phase2_isolated_mutation_command.py` to run that same
guarded read-only preflight and, only if it succeeds, render the exact reviewed
mutation command.

The resulting `mutation_command` is advisory text only. The renderer sets
`mutation_executed=false` and never launches the mutation. If the preflight
fails, is not executed, crosses a read-only boundary, or has no reviewed
mutation flag, no mutation command is produced.

This keeps the operator chain explicit:

1. inspect lifecycle state;
2. render the read-only preflight;
3. execute the guarded read-only preflight;
4. render the reviewed mutation command after a successful preflight;
5. execute any mutation separately, only when explicitly authorized.


## Audit fingerprints

The reviewed mutation preview also emits deterministic SHA-256 fingerprints:

- `preflight_fingerprint` hashes the complete structured guarded-preflight
  report using canonical JSON key ordering;
- `mutation_fingerprint` hashes that preflight fingerprint together with the
  exact rendered mutation argv.

The mutation fingerprint is omitted when no mutation command is rendered.
These fingerprints are audit identifiers only; they do not authorize or
execute a mutation and do not replace the underlying tool's own fail-closed
preflight when a mutation is eventually run.

## Stale mutation preview check

Before using a previously saved mutation preview, run
`deploy/tools/check_phase2_isolated_mutation_freshness.py --preview <file>`.

The checker reruns the guarded read-only preflight and requires the saved and
current preview to agree on:

- lifecycle state, next action, and reviewed tool;
- exact mutation argv;
- `preflight_fingerprint`;
- `mutation_fingerprint`.

If the current preflight is no longer mutation-ready, or any of those values
changed, the saved command is reported stale and the tool exits non-zero.

The checker never executes the mutation. The underlying mutation tool must
still perform its own fail-closed preflight when an explicitly authorized
mutation is eventually run.


## Preview format contract

Mutation previews use `format_version=2` and
`fingerprint_schema=PHASE2_MUTATION_PREVIEW_V2`.

Both preflight and mutation fingerprints are domain-separated by that schema.
A V2 preview records `reviewed_source_commit`,
`deploy_surface_sha256`, and `deploy_surface_files`. Preview generation
requires all tracked files under `deploy/` to match Git HEAD, then hashes
every tracked deploy file deterministically. Untracked runtime cache files are
ignored.

A rendered mutation also records `mutation_tool_sha256`, the SHA-256 of the
exact reviewed Python tool that would receive `--apply` or `--prepare`.
The source identity, deploy-surface digest, and tool digest are part of the
fingerprints and freshness comparison, so unchanged argv cannot make changed
reviewed deployment code look current.

The freshness checker rejects an unsupported format version or fingerprint
schema instead of attempting an implicit migration.

When a future preview format is introduced, regenerate the preview with the
current reviewed tools. Do not rewrite old preview JSON to make it appear
compatible.


## Atomic preview saving

Use `deploy/tools/save_phase2_isolated_mutation_preview.py --output <file>`
instead of shell redirection when you want to persist a mutation preview for
later freshness checks.

The saver first renders a mutation-ready V2 preview, then writes the complete
JSON to a private temporary file, fsyncs it, atomically renames it into place,
sets mode `0600`, and fsyncs the parent directory. Existing previews are not
replaced unless `--replace` is supplied.

The output path must stay outside Pio production/runtime/config paths such as
`/opt/pio`, `/opt/pio/data`, `/opt/pio-phase2-runtime`, `/etc/pio`,
and `/etc/systemd/system`.

Saving the preview is only an audit-artifact write. It does not execute the
rendered mutation, make RPC calls, write the Pio database, or control systemd.


## Fresh preview execution

Use `deploy/tools/execute_phase2_isolated_mutation_preview.py` only after a
V2 mutation preview has been saved with the atomic saver.

The executor requires both:

- `--preview <file>`, pointing at a private `0600` saved preview outside
  production/runtime/config roots; and
- `--expected-preview-sha256 <sha256>`, copied from the saver output.

Without `--execute`, the tool performs only the freshness and identity checks.
It does not launch the mutation.

With `--execute`, the executor:

1. verifies the saved preview bytes match the explicit expected SHA-256;
2. reruns the complete guarded freshness check;
3. requires the same reviewed source commit, deploy-surface digest and file
   count, exact mutation argv, mutation fingerprint, and mutation-tool digest;
4. recomputes the mutation tool and deploy-surface identity immediately before
   launch;
5. verifies the preview file did not change during review;
6. launches the exact current reviewed argv directly, never through a shell;
7. removes direct Solana/Jupiter/Helius credentials from the child environment
   (tools that need RPC load the reviewed env file themselves);
8. never returns raw child stderr; structured JSON output is redacted if it
   contains credential-bearing fields or URL query material.

The `--execute` flag is the explicit mutation boundary. A broad repository
build request does not substitute for that flag and does not authorize a
production mutation.

The underlying mutation tool still performs its own fail-closed preflight. A
fresh preview is therefore necessary but not sufficient for a successful
mutation.


## Two-phase mutation execution receipts

For an auditable mutation launch, prefer
`deploy/tools/run_phase2_isolated_mutation_with_receipt.py` over calling the
fresh-preview executor directly.

Without `--execute`, the wrapper performs the same non-mutating readiness
check and reports the receipt path it would use. It writes nothing.

With `--execute`, it:

1. runs the fresh-preview executor in non-mutating mode;
2. refuses an existing or symlinked receipt path;
3. writes a private `0600` atomic `PENDING` receipt before launch;
4. invokes the fresh-preview executor again with execution enabled, so the
   preview, source, deploy surface, tool bytes, and mutation fingerprint are
   checked a second time immediately before mutation;
5. atomically replaces the receipt with a terminal record.

Terminal receipt states are:

- `COMPLETED`: the mutation subprocess returned; success or non-zero failure is
  explicitly recorded;
- `ABORTED_BEFORE_LAUNCH`: the second guard failed before the mutation runner
  was entered;
- `OUTCOME_UNKNOWN_AFTER_LAUNCH`: the runner was entered but raised before a
  trustworthy structured result was returned.

The receipt stores hashes/identities and minimal outcome metadata. It hashes
the mutation argv instead of persisting the raw argv and never persists child
stderr or the arbitrary structured mutation result. When a reviewed mutation
returns known safety scalars, the receipt may additionally persist a narrow
`mutation_outcome_summary`: booleans such as `applied`,
`rollback_performed`, and `rollback_succeeded`; non-negative
`files_updated`; readiness/side-effect booleans; and a restricted categorical
`failure_step`. Paths, unit lists, diagnostics, URLs, credentials, and unknown
result fields are never copied into that summary.

By default the receipt is written beside the saved preview as
`<preview>.execution.json`. Receipt paths inside Pio production, runtime,
configuration, data, or systemd roots are rejected.


## Mutation receipt post-audit

Use
`deploy/tools/check_phase2_isolated_mutation_execution_receipt.py --execution-receipt <file>`
after a receipt-backed mutation attempt.

The audit is local and read-only. It makes no RPC calls and performs no
database or service mutation.

It verifies:

- the execution receipt is private `0600`, regular, credential-minimal, and
  structurally consistent with its PENDING/terminal outcome model;
- the original saved mutation preview is still present and private;
- the preview bytes still match the SHA-256 recorded in the receipt;
- reviewed source/deploy-surface identity, mutation fingerprint, mutation-tool
  digest, and mutation-argv digest match between preview and receipt;
- the current Phase-2 state through the existing zero-RPC lifecycle handoff.

The report separates three concepts:

- `audit_integrity_valid`: the receipt/preview evidence chain is intact;
- `mutation_succeeded`: the launched reviewed mutation returned a successful
  structured result;
- `post_mutation_verified`: the evidence chain is intact, the mutation
  completed successfully, and the lifecycle state has moved away from the
  preview's pre-mutation state.

A valid audit chain does not turn a failed, aborted, unknown, or PENDING
mutation into a success. Those outcomes remain explicit and the command exits
non-zero.
