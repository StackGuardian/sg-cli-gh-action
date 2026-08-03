# Tirith Policy Check

Evaluate your StackGuardian policies against a terraform plan in CI, and report the outcome as a
pull-request comment and a check run.

```yaml
- run: |
    terraform plan -out=tfplan -input=false
    terraform show -json tfplan > plan.json

- uses: StackGuardian/sg-cli-gh-action@v2
  with:
    sg-api-key: ${{ secrets.SG_API_TOKEN }}
    sg-org: ${{ vars.SG_ORG }}
    input-path: plan.json
    fail-on-error: true
```

```yaml
permissions:
  contents: read
  pull-requests: write   # sticky comment
  checks: write          # check run
```

## What it does

1. **Masks the plan on your runner**, before anything leaves it. Values terraform marked sensitive
   are replaced, root `variables` are dropped wholesale, and `planned_values` and `prior_state` are
   removed entirely.
2. **Packs** the masked documents together with your terraform source into a `tar.gz`, excluding
   `.git`, `.terraform`, `*.tfstate*` and anything in `.gitignore`.
3. **Uploads** it and creates a StackGuardian workflow run, which evaluates the policies your
   organization has scoped to that workflow.
4. **Reports** the verdict: a sticky pull-request comment, a `Tirith Policy` check run, the job
   summary, and action outputs.

Policies live in StackGuardian and are selected server-side by their `EnforcedOn` scope. There are
no policy files in your repository and nothing is evaluated on the runner.

## A note on masking

Terraform's `*_sensitive` markers are **not exhaustive**. A value that flows through `locals`, or
comes from a provider that did not mark its schema, arrives marked `false` and marker-driven
masking will not catch it. Dropping `planned_values` and `variables` limits the blast radius, but
if a value must never leave your infrastructure, do not let it into a plan.

Two related habits worth keeping:

- Write `terraform state pull > state.json`, never `> terraform.tfstate`. With a local backend the
  shell truncates the file terraform is about to read.
- Use `input-kind: terraform_state` for a state document. Plain `json` uploads it unmasked, and
  state holds every attribute in plaintext.

## Inputs

| Input | Required | Default | |
|---|---|---|---|
| `sg-api-key` | yes | | Organization (`sgo_`) token |
| `sg-org` | yes | | Organization name |
| `input-path` | | | Document to evaluate. One of this or `state-path` |
| `input-kind` | | `terraform_plan` | `terraform_plan`, `terraform_state`, `kubernetes`, `json` |
| `state-path` | | | Terraform state, masked before upload |
| `infracost-path` | | | `infracost breakdown --format json` |
| `source-dir` | | `.` | Terraform source packed alongside the documents |
| `fail-on-error` | | `false` | Fail the job when a policy fails |
| `comment` / `check` | | `true` | Post the comment / check run |
| `comment-tag` | | `default` | Namespaces the comment and the archive |
| `timeout` | | `1800` | Seconds to wait for the run |
| `workflow-id` | | derived | Overrides `github-com-<org>-<repo>-<workflow>` |
| `terraform-version` | | | Recorded on the workflow at creation |
| `step-template-id` | | platform default | Override the terraform step template |
| `tirith-version` | | `1.2.0` | Pin the CLI version |
| `sg-api-url` / `sg-dashboard-url` | | prod | Set both together for other regions |

## Outputs

`verdict` (`passed` \| `warned` \| `failed` \| `errored` \| `no-policies` \| `approval-required`),
`passed`, `failed`, `warned`, `results`, `results-file`, `wfrun-id`, `wfrun-url`, `comment-id`.

## Exit codes

`fail-on-error` governs **policy verdicts**, not tool health.

| | `fail-on-error: false` | `fail-on-error: true` |
|---|---|---|
| Policies pass or warn | green | green |
| A policy fails | green | **red** |
| Run errored, platform unreachable, no verdict | **red** | **red** |

The last row is deliberate: a run that never produced a verdict must never look like a pass.

## Matrix and monorepo usage

Give each leg its own `workflow-id` **and** `comment-tag`:

```yaml
strategy:
  fail-fast: false
  matrix:
    stack: [dev, prod]
steps:
  - uses: StackGuardian/sg-cli-gh-action@v2
    with:
      sg-api-key: ${{ secrets.SG_API_TOKEN }}
      sg-org: ${{ vars.SG_ORG }}
      input-path: ${{ matrix.stack }}/plan.json
      source-dir: ${{ matrix.stack }}
      workflow-id: infra-${{ matrix.stack }}
      comment-tag: ${{ matrix.stack }}
```

Both are load-bearing. Runs on a single StackGuardian workflow serialize while one is pending, so
without a distinct `workflow-id` a 20-leg matrix becomes a 20-deep queue. And the sticky comment is
found by a marker containing the tag, so shared tags mean the legs overwrite each other's comment.

## Migrating from the sg-cli action

Version 1 of this action was a thin `sg-cli` passthrough with a single `operation` input. It is
unrelated to what this action does now. Pin `@v1.0.0-beta` to keep the old behaviour; there is no
automatic migration.

## Where the code lives

The action is a wrapper. Everything that talks to StackGuardian is `tirith platform check` in
[StackGuardian/tirith](https://github.com/StackGuardian/tirith) — so the same behaviour is
available from GitLab, a Makefile or a laptop:

```
tirith platform check --org acme --workflow-id infra --input-path plan.json --fail-on-error
```

What is left in this repository is only what is genuinely GitHub-specific: reading the event
payload, posting the comment and check run, and setting action outputs.
