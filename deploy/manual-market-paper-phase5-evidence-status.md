# Manual market/PAPER Phase 5 evidence status

This stage evaluates the existing reviewed Phase 5 promotion criteria after a
successful bounded manual PAPER proof cycle.

Reviewed source:

```text
5e6407fce192a696aa59aca5ecfae812151bb8e1
```

Reviewed tool blob:

```text
f3b90f3100c23f8454172f3bb97482e31df5921d
```

This is a **read-only evidence-status stage**. It does not persist Phase 5
promotion and does not authorize recurring PAPER automation.

## Prerequisite

Start from a ready one-cycle post-cycle audit produced by
`check_manual_market_paper_one_cycle_post_cycle.py`.

That artifact must already prove:

- the bounded one-shot PAPER cycle completed under its signed scope;
- the post-cycle PAPER ledger reconciles;
- the authorized account/run identity is intact;
- production activation state remains unchanged; and
- timer/service/cursor/signing/submission/live-capital authorization remains
  false.

## Run

Use an isolated checkout of the reviewed source:

```bash
set -euo pipefail

REVIEWED_REF="5e6407fce192a696aa59aca5ecfae812151bb8e1"
SRC="$(mktemp -d /var/tmp/pio-phase5-evidence-source.XXXXXX)"

POST_CYCLE_AUDIT="/var/tmp/pio-manual-paper-one-cycle-post-cycle-audit.json"
PHASE5_STATUS="/var/tmp/pio-manual-paper-phase5-evidence-status.json"

git clone --quiet https://github.com/Dtwosam/Pio.git "$SRC"
git -C "$SRC" checkout --quiet --detach "$REVIEWED_REF"
test "$(git -C "$SRC" rev-parse HEAD)" = "$REVIEWED_REF"

python3 "$SRC/deploy/tools/check_manual_market_paper_phase5_evidence_status.py" \
  --repo /opt/pio \
  --source-tree "$SRC" \
  --post-cycle-audit "$POST_CYCLE_AUDIT" \
  > "$PHASE5_STATUS"

python3 -m json.tool "$PHASE5_STATUS"
```

## Database boundary

The evaluator never instantiates Pio `Storage` on the production database.

It opens production SQLite read-only and creates an online-backup snapshot in a
private temporary directory. If no WAL exists, the source connection uses
SQLite immutable mode so inspection does not create an empty WAL. If a WAL is
present, WAL-aware read-only mode is used so committed WAL state is included.

The production database and WAL are SHA-256 checked before and after the
evaluation. Any drift fails the status build closed.

## Exact Phase 5 criteria

The tool uses the existing `Phase5PromotionCriteria` defaults without
lowering them:

- at least 72 runtime hours;
- at least 500 terminal PAPER ticks;
- at least 99% non-failure tick rate;
- at most 5% dependency-blocked tick rate;
- at most 1 consecutive failure;
- zero stale RUNNING ticks;
- at least 100 applied chain valuations;
- at least 3 distinct valued positions;
- at least 3 closed positions;
- at least 2 distinct valued pools;
- a passing decimal-exact PAPER ledger audit; and
- persistent Phase 3 promotion.

The status artifact seals the exact criteria, endurance report, ledger audit,
closed-position/pool counts, and the exact current blocking reasons.

## Interpreting the result

`phase5_evidence_status_ready=true` means the evidence was evaluated
successfully and the source production DB/WAL did not change during inspection.

It does **not** mean Phase 5 is ready.

When evidence is still incomplete:

```text
phase5_promotion_ready=false
requires_additional_paper_evidence=true
```

Review `phase5_reasons` for the exact blockers.

If all reviewed criteria eventually pass, the artifact may report:

```text
phase5_promotion_ready=true
requires_additional_paper_evidence=false
```

Even then it must still contain:

```text
phase5_promotion_persisted=false
phase5_promotion_authorized=false
recurring_paper_automation_authorized=false
paper_timer_enable_authorized=false
live_capital_authorized=false
```

## Stop boundary

Stop here.

This artifact is evidence for the next decision, not the decision itself.

A later implementation may separately review Phase 5 promotion persistence
only when the evidence is actually ready. Any recurring PAPER evidence timer
also remains a separate authorization surface and must validate its systemd
unit, account, environment overrides, cadence, and PAPER-only scope.

No service, timer, detector cursor, source file, Git state, transaction, or
live capital is changed by this runbook.
