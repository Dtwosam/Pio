# Phase 2 isolated next-command preview

Use `deploy/tools/render_phase2_isolated_next_command.py` when you want a
copyable command for the current lifecycle handoff without manually translating
its structured parameters into CLI flags.

The renderer calls the zero-RPC lifecycle handoff and renders only the next
tool's **read-only preflight command**.

## Mutation boundary

If the next reviewed step has a mutation mode, the renderer reports it in
`mutation_flag`:

- `--apply` for bootstrap, staging, systemd installation, activation, smoke,
  timer activation, or autopause;
- `--prepare` for isolated runtime preparation.

The mutation flag is never appended to `preflight_argv` or
`preflight_command`. The renderer never executes either command.

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
