# Terragrunt pipelines

> Design notes and a phased plan. Phase 1 works with the action as it ships today; phases 2–3 are
> proposals.

## The short answer to "do we need multiple states and plans?"

**Yes — one of each per unit, and that is not a workaround, it is terragrunt's model.**

Every terragrunt unit (a directory with a `terragrunt.hcl`) is a separate terraform root module with
its **own backend and its own state**. `run-all plan` runs N independent plans in dependency order.
There is no combined plan document to evaluate, and producing one would be wrong: two units can
legitimately hold resources with identical addresses.

So the shape is `N units → N plan JSONs → N policy evaluations`, and the real design questions are
about how those N map onto StackGuardian workflows, artifacts, and PR comments.

## What exists today, and the gap worth knowing about

The platform's terraform step supports terragrunt (`terragruntBinPath`, `run-all`), **but
`run-all` skips policy evaluation entirely**:

```python
# workflow-step-templates/terraform/main.py:1331
if terragruntRunAllEnabled:
    terragrunt_plan_apply_destroy_all(...)
    return          # <-- returns before execute_policies() is ever reached
```

There is no `tf_plan.json` produced on that path and therefore nothing to evaluate. So a terragrunt
`run-all` workflow in StackGuardian today enforces **no IaC policies at all**.

That reframes this work: the action is not catching up to the terraform step here, it is closing a
gap the platform has.

## Getting one plan JSON per unit

The mechanism depends on the terragrunt version, and the flag names changed in the CLI redesign:

| Terragrunt | Command |
|---|---|
| ≥ v0.73 (`run --all`) | `terragrunt run --all plan --out-dir=plans` then `terragrunt run --all show -json --json-out-dir=json-plans` |
| ~v0.68–0.72 | `terragrunt run-all plan --terragrunt-out-dir=plans` / `--terragrunt-json-out-dir=json-plans` |
| older | loop over units yourself: `terragrunt-info` or `find . -name terragrunt.hcl` and run `plan`/`show -json` per directory |

All three yield a directory tree mirroring the unit paths, one JSON per unit. **Pin the flag
spelling to the terragrunt version you install** — this is the most likely thing to break silently,
because an unrecognised `--out-dir` is accepted by some versions as a passthrough to terraform.

The loop fallback is worth keeping in the docs regardless: it is version-independent and easy to
reason about.

## Phase 1 — works today, no code changes

Discover units, then matrix over them. This is exactly the pattern already verified in
`examples/monorepo-matrix.yml`, scaled up by generating the matrix instead of hard-coding it.

```yaml
name: terragrunt-policy

on: [pull_request]

permissions:
  contents: read
  pull-requests: write
  checks: write

jobs:
  discover:
    runs-on: ubuntu-latest
    outputs:
      units: ${{ steps.find.outputs.units }}
    steps:
      - uses: actions/checkout@v4
      - id: find
        run: |
          # Only units that actually changed in this PR. Planning every unit on a large repo is
          # slow and mostly noise; scope to what the PR touches.
          units=$(find live -name terragrunt.hcl -not -path '*/.terragrunt-cache/*' \
                  | xargs -n1 dirname | sort -u | jq -R . | jq -sc .)
          echo "units=$units" >> "$GITHUB_OUTPUT"

  policy:
    needs: discover
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false          # one unit failing must not hide the others
      max-parallel: 5           # be kind to the API and to your own rate limits
      matrix:
        unit: ${{ fromJSON(needs.discover.outputs.units) }}
    steps:
      - uses: actions/checkout@v4
      - uses: gruntwork-io/terragrunt-action@v2
        with: { tg_version: '0.73.0', tf_version: '1.9.0', tg_dir: ${{ matrix.unit }}, tg_command: 'plan -out=tfplan' }

      - run: terragrunt show -json tfplan > plan.json
        working-directory: ${{ matrix.unit }}

      - uses: StackGuardian/tirith-iac-governance-action@v2
        with:
          sg-api-key: ${{ secrets.SG_API_TOKEN }}
          sg-org: ${{ vars.SG_ORG }}
          input-path: ${{ matrix.unit }}/plan.json
          # One StackGuardian workflow per unit: mirrors "one state per unit", gives per-unit run
          # history, and — critically — stops units queueing behind each other, since runs on a
          # single workflow serialize.
          workflow-id: tg-${{ github.repository_owner }}-${{ github.event.repository.name }}-${{ matrix.unit }}
          # One sticky comment per unit; without this every leg overwrites the same comment.
          comment-tag: ${{ matrix.unit }}
          fail-on-error: true
```

**Two things that are not optional here.**

`workflow-id` per unit: runs on one StackGuardian workflow serialize while one is pending, so
without this a 20-unit matrix becomes a 20-deep queue.

`comment-tag` per unit: the sticky comment is found by a marker containing the tag, so shared tags
mean the legs overwrite each other and you see only whichever finished last.

The action slugifies `workflow-id`, so `live/prod/vpc` becomes `live-prod-vpc`, and logs the
rewrite. Supply something already slug-shaped if you want it predictable.

### What Phase 1 costs you

- **N comments on the PR.** Fine at 3 units, poor at 20.
- **N workflows in StackGuardian.** Correct, but the list gets long.
- **`EnforcedOn` per unit.** A policy scoped to one workflow does not cover the others. Scope
  org-wide (`*`) or to the workflow group instead, or you will be editing policy scopes every time
  someone adds a unit.

## Phase 2 — one aggregated comment (small change)

Turn commenting off per leg, collect the `results` outputs, and post once.

```yaml
      - uses: StackGuardian/tirith-iac-governance-action@v2
        id: tirith
        with:
          # ... as above ...
          comment: false                    # defer reporting
          check: false
          fail-on-error: false              # aggregate the verdict instead
      - uses: actions/upload-artifact@v4
        with:
          name: tirith-${{ strategy.job-index }}
          path: ${{ steps.tirith.outputs.results }}   # (needs a results-file output — see below)

  report:
    needs: policy
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { pattern: 'tirith-*', merge-multiple: true, path: results }
      - uses: StackGuardian/tirith-iac-governance-action/comment@v2
        with:
          results-glob: 'results/*.json'
          group-by: unit
```

This needs two additions to the action:

1. **A `results-file` output** (a path) alongside the existing `results` (the JSON itself).
   `$GITHUB_OUTPUT` is fine for one plan's findings but is not where a 20-unit aggregate belongs.
2. **A standalone `comment/` sub-action** that renders many result documents into one comment,
   grouped by unit. The renderer already summarises and truncates; it needs a grouping level above
   "policy".

Rough shape of the aggregated comment:

```markdown
## 🛡️ Tirith — 2 units failed, 18 passed

| Unit | Failed | Warned | Passed |
|---|---|---|---|
| `live/prod/vpc` | 1 | 0 | 4 |
| `live/prod/eks` | 1 | 2 | 3 |
| _18 others_ | 0 | 0 | 72 |

<details><summary><strong>❌ live/prod/vpc</strong></summary>
… findings …
</details>
```

**Effort: S–M.** The renderer already has the hard parts (summarising, truncation, sticky marker).

## Phase 3 — native multi-unit support (proposal)

Let one invocation accept many documents:

```yaml
- uses: StackGuardian/tirith-iac-governance-action@v2
  with:
    input-glob: 'live/**/plan.json'
    unit-from-path: 'live/(?<unit>.+)/plan.json'
    workflow-id-template: 'tg-{repo}-{unit}'
```

The action would then fan out uploads and runs itself, poll them concurrently, and post one
comment. That removes the matrix boilerplate entirely and makes "20 units" a single job.

**Effort: L**, and it duplicates scheduling that GitHub Actions already does well. Worth it only if
Phase 1/2 prove too clumsy in practice — I would not build it speculatively.

## State: send it, per unit

Same rule as the single-module case, once per unit, after apply:

```yaml
- run: terragrunt state pull > state.json     # NOT terraform.tfstate -- see below
  working-directory: ${{ matrix.unit }}
- uses: StackGuardian/tirith-iac-governance-action@v2
  with:
    input-path: ${{ matrix.unit }}/state.json
    input-kind: terraform_state               # masks before upload
    workflow-id: tg-...-${{ matrix.unit }}
    comment-tag: ${{ matrix.unit }}-state     # distinct tag => distinct artifact folder
```

Three traps, all of which bit during testing of the non-terragrunt pipeline:

- **Never `state pull > terraform.tfstate`.** With a local backend that is the file terragrunt is
  about to read, and the shell truncates it first. Use a different name.
- **`input-kind: terraform_state`, not `json`.** Plain `json` uploads the document unmasked; state
  holds every attribute in plaintext.
- **A distinct `comment-tag` per phase.** It namespaces the artifact folder as well as the comment,
  so the plan and state uploads for one commit do not overwrite each other.

## Dependencies between units

Terragrunt's `dependency` blocks mean a unit's plan can depend on another unit's *outputs*. On a PR
where the dependency has not been applied yet, terragrunt either fails or substitutes
`mock_outputs`.

Policy evaluation sees only what the plan says, so **a plan built on mocked outputs can pass a
policy that the real apply would fail.** There is no clean fix at the action layer; the honest
handling is to make it visible:

- Prefer `--terragrunt-fetch-dependency-output-from-state` where the dependency is already applied.
- Where `mock_outputs` are in play, treat the result as advisory for that unit.
- Consider re-checking post-apply (the state phase above), which sees real values.

Worth stating plainly in whatever docs ship with this rather than discovering it in an incident.

## Suggestions, in the order I would do them

1. **Ship Phase 1 as a documented example** (`examples/terragrunt-matrix.yml`). Zero code, and it
   works with the action as it stands. Validates the shape before investing in aggregation.
2. **Add `results-file` output + the `comment/` sub-action** (Phase 2). This is the change that
   makes large repos usable, and it is small.
3. **Fix the platform gap separately.** Terragrunt `run-all` returning before `execute_policies` is
   a bug in the terraform step regardless of this action. Either produce per-unit plan JSONs there
   too, or document that `run-all` workflows are unpoliced.
4. **Decide the `EnforcedOn` story for many units** before anyone onboards a real monorepo. Per-unit
   workflows plus per-workflow policy scoping does not scale; org-wide or workflow-group scoping
   does. This is a platform question, not an action one.
5. **Leave Phase 3 alone** until 1 and 2 have real usage behind them.
